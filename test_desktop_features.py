import gc
import json
import logging
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from sentinel_desktop import DesktopPreferences, NoteStore, DEFAULT_HOTKEYS, ROOT
from sentinel_live import create_parser
from sentinel_panel import SentinelPanel
from sentinel_windows import HotkeyRegistry, HOTKEY_IDS, parse_hotkey


class FakeWindowsAPI:
    def __init__(self):
        self.held = {}
        self.unregistered = []
    def RegisterHotKey(self, hwnd, ident, modifiers, vk):
        spec = (modifiers, vk)
        if spec in self.held:
            return False
        self.held[spec] = hwnd, ident
        return True
    def UnregisterHotKey(self, hwnd, ident):
        self.unregistered.append((hwnd, ident))
        for spec, owner in list(self.held.items()):
            if owner == (hwnd, ident):
                del self.held[spec]
        return True


class HotkeyTests(unittest.TestCase):
    def test_default_chords_and_norepeat(self):
        self.assertEqual(parse_hotkey(DEFAULT_HOTKEYS['quick_note']), (0x4007, ord('N')))
        self.assertEqual(parse_hotkey('ctrl+alt+shift+space'), (0x4007, 32))
        self.assertEqual(parse_hotkey('Ctrl+F9'), (0x4002, 0x78))
    def test_invalid_or_typing_shortcuts_rejected(self):
        for chord in ('N', 'Shift+N', 'Ctrl+F12', 'Ctrl+Ctrl+N', 'Ctrl+NotAKey'):
            with self.subTest(chord=chord), self.assertRaises(ValueError):
                parse_hotkey(chord)
    def test_duplicate_actions_rejected_before_mutating_registration(self):
        api = FakeWindowsAPI()
        registry = HotkeyRegistry(api, 1)
        registry.apply(DEFAULT_HOTKEYS)
        with self.assertRaises(ValueError):
            registry.apply({'quick_note':'Ctrl+N','toggle_window':'Control+N'})
        self.assertEqual(len(api.held), 4)
    def test_conflict_keeps_other_startup_bindings_and_can_close(self):
        api = FakeWindowsAPI()
        spec = parse_hotkey(DEFAULT_HOTKEYS['quick_note'])
        api.held[spec] = (99, 99)
        registry = HotkeyRegistry(api, 1)
        result = registry.apply(DEFAULT_HOTKEYS)
        self.assertFalse(result['success'])
        self.assertIn('quick_note', result['errors'])
        self.assertEqual(len(result['active']), 3)
        registry.close()
        registry.close()
        self.assertEqual(api.held, {spec:(99,99)})
    def test_failed_rebind_restores_previous_and_successful_rebind_releases_old(self):
        api = FakeWindowsAPI()
        registry = HotkeyRegistry(api, 1)
        registry.apply(DEFAULT_HOTKEYS)
        mapping = DEFAULT_HOTKEYS | {'quick_note':'Ctrl+Alt+F9'}
        api.held[parse_hotkey('Ctrl+Alt+F9')] = (99,99)
        result = registry.apply(mapping)
        self.assertFalse(result['success'])
        self.assertEqual(registry.active, DEFAULT_HOTKEYS)
        del api.held[parse_hotkey('Ctrl+Alt+F9')]
        self.assertTrue(registry.apply(mapping)['success'])
        self.assertNotIn(parse_hotkey(DEFAULT_HOTKEYS['quick_note']), api.held)
        registry.close()
        self.assertFalse(api.held)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT, prefix='.sentinel-test-')
        self.path = Path(self.temp.name)
        self.assertEqual(self.path.resolve().parent, ROOT)
    def tearDown(self):
        self.temp.cleanup()
    def test_preferences_roundtrip_and_corrupt_file_preserved(self):
        path = self.path/'preferences.json'
        prefs = DesktopPreferences(path)
        prefs.save(topmost=False, hotkeys=DEFAULT_HOTKEYS | {'quick_note':'Ctrl+Alt+F9'})
        reread = DesktopPreferences(path)
        self.assertFalse(reread.value['topmost'])
        self.assertEqual(reread.value['hotkeys']['quick_note'], 'Ctrl+Alt+F9')
        path.write_text('{broken', encoding='utf-8')
        with self.assertLogs(level='ERROR'):
            self.assertTrue(DesktopPreferences(path).error)
        self.assertEqual(path.read_text(), '{broken')
    def test_long_private_notes_keep_text_kind_session_and_never_become_speech(self):
        store = NoteStore(self.path)
        text = '不完整的想法…\n' * 10000
        for kind in ('我在想','我想知道','我想说'):
            store.save(text, kind, 'session-A')
        store.save('别的场次', '我在想', 'session-B', draft=True)
        rows = NoteStore(self.path).read('session-A')
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['text'], text)
        self.assertEqual({r['note_kind'] for r in rows}, {'我在想','我想知道','我想说'})
        self.assertTrue(all(r['spoken'] is False and r['send_to_llm'] is False for r in rows))
        self.assertTrue(all(r['type'] == 'private_note' for r in rows))
        self.assertEqual(len({r['note_id'] for r in rows}), 3)
        store.bind_session('session-A', 'logs/session-real.jsonl')
        self.assertEqual(json.loads((self.path/'sessions.jsonl').read_text())['asr_log'], 'logs/session-real.jsonl')
    def test_torn_tail_does_not_hide_saved_notes(self):
        store = NoteStore(self.path)
        store.save('已保存', '我在想', 's')
        with (self.path/'notes.jsonl').open('a',encoding='utf-8') as file:
            file.write('{incomplete')
        with self.assertLogs(level='WARNING'):
            self.assertEqual(store.read()[0]['text'], '已保存')
    def test_extension_failure_does_not_undo_persistence(self):
        def bad(record): raise RuntimeError('subscriber failed')
        store = NoteStore(self.path, on_note_saved=bad)
        with self.assertLogs(level='ERROR'):
            store.save('本地仍保留', '我想说', 's')
        self.assertEqual(len(store.read()), 1)


class FakeDesktop:
    tray_available = True
    thread = None
    def __init__(self):
        self.closed = threading.Event()
        self.statuses, self.close_calls = [], 0
    def update_status(self, *status):
        self.statuses.append(status)
    def close(self):
        self.close_calls += 1
        self.closed.set()


class InteractionTests(unittest.TestCase):
    def setUp(self):
        gc.collect(); gc.disable()
        self.temp = tempfile.TemporaryDirectory(dir=ROOT, prefix='.sentinel-test-')
        self.path = Path(self.temp.name)
        self.assertEqual(self.path.resolve().parent, ROOT)
        self.root = tk.Tk(); self.root.withdraw()
        self.calls = []
        def runner(args, on_event, stop_event):
            self.calls.append(args)
            on_event(kind='session', log_path=str(self.path/'asr.jsonl'))
            on_event(kind='listening')
            stop_event.wait(3)
        args = create_parser().parse_args(['--llm'])
        self.panel = SentinelPanel(self.root, args, runner,
                                   preferences=DesktopPreferences(self.path/'settings.json'), note_store=NoteStore(self.path/'notes'))
        self.panel.desktop = FakeDesktop()
    def tearDown(self):
        self.panel.controller.stop()
        if self.panel.controller.thread:
            self.panel.controller.thread.join(3)
            self.assertFalse(self.panel.controller.thread.is_alive())
        try: self.root.destroy()
        except tk.TclError: pass
        self.root.report_callback_exception = None
        self.panel = self.root = None
        gc.collect(); gc.enable()
        self.temp.cleanup()
    def wait(self, predicate):
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            self.root.update()
            if predicate(): return
            time.sleep(.01)
        self.fail('desktop wait timed out')
    def test_fold_expand_hide_show_keep_backend_idle(self):
        p = self.panel
        self.assertEqual(p.layout_mode, 'compact')
        p.toggle_fold(); self.assertEqual(p.layout_mode, 'bar')
        self.assertEqual(p.body.winfo_manager(), '')
        p.toggle_fold(); self.assertEqual(p.layout_mode, 'compact')
        p.toggle_detail(); self.assertEqual(p.layout_mode, 'detail')
        self.assertEqual(p.detail.winfo_manager(), 'pack')
        p.hide_window(); self.assertTrue(p.hidden)
        p.show_window(); self.assertFalse(p.hidden)
        self.assertEqual(self.root.state(), 'normal')
        self.assertFalse(self.calls)

    def test_graph_collects_full_session_before_open_and_separates_restart(self):
        p=self.panel
        p.start();self.wait(lambda:p.mic_on)
        first=p.logic_document
        for i in range(1,151):
            p.handle(dict(kind='final',turn_id=i,text='已确认原话',speaker_id='B',role='对方',start_s=i,end_s=i+1))
        self.assertEqual(len(p.turns),128)
        self.assertEqual(len(first.turns),150)
        self.assertIsNone(p.logic_service)  # No graph API request before opening.
        p.stop();self.wait(lambda:not p.controller.busy)
        p.start();self.wait(lambda:p.mic_on)
        self.assertIsNot(first,p.logic_document)
        self.assertFalse(p.logic_document.turns)

    def test_exit_flushes_last_asr_turn_before_final_graph_save(self):
        from types import SimpleNamespace
        p=self.panel;saved=[]
        def runner(args,on_event,stop_event):
            on_event(kind='listening');stop_event.wait(3)
            on_event(kind='final',turn_id=1,text='停止前的最后一句',speaker_id='unknown',role='未绑定')
        p.controller.runner=runner
        p.start();self.wait(lambda:p.mic_on)
        service=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:False),
                                stop=lambda:saved.append(list(p.logic_document.turns)))
        p.logic_workers=[service]
        p.request_exit();self.wait(lambda:p.desktop.closed.is_set())
        self.assertTrue(saved)
        self.assertEqual(saved[0],[1])

    def test_floating_bar_controls_keep_same_session_and_show_real_mic_state(self):
        p = self.panel
        self.root.deiconify()
        p.start_button.invoke()
        self.wait(lambda:p.mic_on)
        session = p.controller.session_id
        p.set_layout('bar')
        self.wait(lambda:'麦克风开' in p.mini_listen_button.cget('text'))
        self.assertTrue(self.root.overrideredirect())
        self.assertEqual(len(self.calls),1)
        self.assertEqual(p.controller.session_id,session)
        p.mini_listen_button.invoke()
        self.wait(lambda:not p.controller.busy)
        self.wait(lambda:'麦克风关' in p.mini_listen_button.cget('text'))
        p.mini_listen_button.invoke()
        self.wait(lambda:p.mic_on)
        self.assertEqual(len(self.calls),2)
        self.assertNotEqual(p.controller.session_id,session)
        p.set_layout('detail')
        self.assertFalse(self.root.overrideredirect())
        self.assertEqual(p.goal_editor.winfo_manager(),'pack')
        p.set_layout('compact')
        self.assertEqual(p.goal_editor.winfo_manager(),'')
        self.assertEqual(p.goal_summary.winfo_manager(),'pack')

    def test_small_views_keep_microphone_buttons_inside_window(self):
        p = self.panel
        self.root.deiconify()
        for mode in ('compact','bar','detail','compact'):
            p.set_layout(mode)
            self.root.update()
            p.fit_content()
            self.root.update()
            controls = (p.mini_listen_button,) if mode=='bar' else (p.start_button,p.stop_button,p.expand_button)
            for widget in controls:
                self.assertTrue(widget.winfo_ismapped(),(mode,widget))
                self.assertGreaterEqual(widget.winfo_rootx(),self.root.winfo_rootx())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
                self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())
            if mode!='bar':
                self.assertLessEqual(p.action.winfo_rooty()+p.action.winfo_height(),p.card_shell.winfo_rooty()+p.card_shell.winfo_height())
            if mode=='compact':
                self.assertLessEqual(self.root.winfo_height(),520,'Idle floating view must stay compact')

    def test_alert_and_unknown_speaker_survive_view_changes_without_fake_summary(self):
        p = self.panel
        p.handle({'kind':'alert','headline':'对方提出延期，尚未得到我方同意','summary':'需确认是否接受','action':'先确认日期，再明确答复'})
        p.handle({'kind':'speaker_segment','speaker_id':'unknown','speakers':{}})
        p.set_layout('bar')
        from sentinel_ui import refresh_view
        refresh_view(p)
        self.assertIn('对方提出延期',p.bar_text.get())
        self.assertIn('unknown',p.speaker_status.get())
        self.assertIn('0 个确认片段',p.session_summary.get())
        p.show_error('后台连接失败')
        refresh_view(p)
        self.assertIn('后台连接失败',p.bar_text.get())
        p.set_layout('detail')
        self.assertEqual(p.headline.cget('text'),'对方提出延期，尚未得到我方同意')
        self.assertIn('后台连接失败',p.error.get())
    def test_close_hides_while_listening_then_tray_exit_cleans_up(self):
        p = self.panel
        p.start(); self.wait(lambda:p.mic_on)
        p.close()
        self.assertTrue(p.hidden)
        self.assertFalse(p.controller.stop_event.is_set())
        self.assertTrue(p.mic_on)
        p.desktop_action('exit')
        self.wait(lambda: p.desktop.closed.is_set())
        self.assertFalse(p.controller.busy)
        self.assertFalse(p.mic_on)
        self.assertEqual(p.desktop.close_calls, 1)
    def test_global_actions_use_existing_single_session_and_goal(self):
        p = self.panel
        p.goal.set('确认付款期限')
        p.desktop_action('toggle_listening'); p.start()
        self.wait(lambda:p.mic_on)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].goal, '确认付款期限')
        first = p.controller.session_id
        p.desktop_action('toggle_listening')
        self.wait(lambda:not p.controller.busy)
        p.desktop_action('toggle_listening')
        self.wait(lambda:len(self.calls)==2)
        self.assertNotEqual(first, p.controller.session_id)
        p.stop(); self.wait(lambda:not p.controller.busy)
    def test_notes_during_listening_dont_call_backend_or_stop_microphone(self):
        p = self.panel
        p.start(); self.wait(lambda:p.mic_on)
        p.desktop_action('quick_note'); self.root.update()
        self.wait(lambda:self.root.focus_get() == p.notes.editor)
        p.notes.kind.set('我想说')
        p.notes.editor.insert('1.0','还没说出口的想法\n' * 1000)
        note = p.notes.save()
        self.assertEqual(note['session_id'], p.controller.session_id)
        self.assertEqual(note['asr_log'], str(self.path/'asr.jsonl'))
        self.assertFalse(note['send_to_llm'])
        self.assertTrue(p.mic_on)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(p.controller.stop_event.is_set())
    def test_note_draft_close_restore_and_exit_persistence(self):
        p = self.panel
        p.quick_note(); self.root.update()
        p.notes.editor.insert('1.0','未完成…')
        p.notes.close(); p.notes.open()
        self.assertEqual(len(p.note_store.read()), 1)
        p.notes.history.current(0); p.notes.restore()
        self.assertEqual(p.notes.editor.get('1.0','end-1c'), '未完成…')
        p.notes.editor.insert('end','继续')
        p.request_exit()
        self.wait(lambda:p.desktop.closed.is_set())
        rows = NoteStore(self.path/'notes').read()
        self.assertTrue(rows[-1]['draft'])
        self.assertEqual(rows[-1]['text'], '未完成…继续')
    def test_exit_write_failure_preserves_editor_and_control(self):
        p = self.panel
        p.quick_note(); p.notes.editor.insert('1.0','不能丢失')
        with patch.object(p.note_store,'save',side_effect=OSError('disk failed')):
            p.request_exit()
        self.assertFalse(p.closing)
        self.assertEqual(p.notes.editor.get('1.0','end-1c'), '不能丢失')
        self.assertIn('未保存', p.error.get())
        p.toggle_fold(); self.assertEqual(p.layout_mode, 'bar')
    def test_backend_failure_still_allows_notes_and_layout(self):
        p = self.panel
        def fail(*a, **k): raise RuntimeError('backend failure')
        p.controller.runner = fail
        with self.assertLogs(level='ERROR'):
            p.start(); self.wait(lambda:not p.controller.busy)
        self.assertEqual(p.phase, '错误')
        p.quick_note(); p.notes.editor.insert('1.0','错误时也能写')
        self.assertIsNotNone(p.notes.save())
        p.toggle_detail(); self.assertEqual(p.layout_mode,'detail')
    def test_desktop_registers_no_hotkeys_even_with_legacy_preferences(self):
        p = self.panel
        self.assertTrue(p.preferences.value['hotkeys'])
        with patch('sentinel_windows.WindowsDesktop') as native:
            p.enable_native()
            self.assertEqual(native.call_args.kwargs['hotkeys'],{})
            native.return_value.start.assert_called_once()
        p.desktop = FakeDesktop()
        p.open_desktop_settings()
        self.assertFalse(hasattr(p,'hotkey_entries'))
        p.quick_note(); self.assertTrue(p.notes.window.winfo_exists())

    def test_settings_and_note_save_controls_are_visible(self):
        p = self.panel
        self.root.deiconify()
        p.open_desktop_settings()
        p.quick_note()
        self.root.update()
        for widget,window in ((p.desktop_exit_button,p.desktop_settings_window),(p.notes.save_button,p.notes.window)):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),window.winfo_rooty()+window.winfo_height())



if __name__ == '__main__': unittest.main()
