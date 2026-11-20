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
        p.show_window()
        p.toggle_fold(); self.assertTrue(p.hidden)
        p.toggle_fold(); self.assertFalse(p.hidden)
        self.assertEqual(p.outer.winfo_manager(), 'pack')
        p.toggle_detail(); self.assertEqual(p.layout_mode, 'detail')
        self.assertEqual(p.detail.winfo_manager(), 'pack')
        p.hide_window(); self.assertTrue(p.hidden)
        p.show_window(); self.assertFalse(p.hidden)
        self.assertEqual(self.root.state(), 'normal')
        self.assertFalse(self.calls)

    def test_reading_views_stay_opaque_without_restarting_session(self):
        p = self.panel
        self.root.deiconify()
        p.start(); self.wait(lambda: p.mic_on)
        session_id = p.controller.session_id
        self.wait(lambda: float(self.root.attributes('-alpha')) == 1)
        p.set_layout('detail')
        self.wait(lambda: float(self.root.attributes('-alpha')) == 1)
        p.set_layout('compact')
        self.wait(lambda: float(self.root.attributes('-alpha')) == 1)
        p.set_layout('bar')
        self.wait(lambda: float(self.root.attributes('-alpha')) < 1)
        p.translucent.set(False)
        p.apply_translucency()
        self.wait(lambda: float(self.root.attributes('-alpha')) == 1)
        self.assertFalse(DesktopPreferences(self.path/'settings.json').value['translucent'])
        p.set_layout('compact')
        self.root.update()
        self.assertEqual(float(self.root.attributes('-alpha')), 1)
        self.assertEqual(p.controller.session_id, session_id)
        self.assertEqual(len(self.calls), 1)

    def qt_click(self, button):
        button.click()
        self.wait(lambda: not self.panel.meeting_overlay._commands)

    def meeting(self):
        self.panel.show_window()
        self.panel.set_layout('meeting')
        self.root.update()
        self.panel.meeting_overlay.app.processEvents()
        return self.panel.meeting_overlay.window

    def test_meeting_glass_controls_share_session_and_restore_opaque_tools(self):
        from PySide6.QtCore import Qt
        p = self.panel
        overlay = self.meeting()
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertTrue(overlay.testAttribute(Qt.WA_TranslucentBackground))
        self.assertEqual(overlay.windowOpacity(), 1)  # Only paint has alpha.
        self.assertFalse(overlay.windowFlags() & Qt.WindowStaysOnTopHint)
        self.qt_click(overlay.listen); self.wait(lambda: p.mic_on)
        session = p.controller.session_id
        self.wait(lambda: overlay.listen.icon_name == 'stop')
        p.error.set('分析服务暂不可用，监听继续。')
        self.wait(lambda: '分析服务' in overlay.headline_value)
        self.assertIn('监听中', overlay.status.text())
        self.assertGreaterEqual(overlay.status.width(), overlay.status.fontMetrics().horizontalAdvance(overlay.status.text()))
        p.hide_window()
        with patch('sentinel_meeting_window.time.monotonic', return_value=p.mic_started+65):
            overlay.refresh()
            self.assertIn('00:01:05', overlay.status.text())
        p.show_window()
        p.error.set('')
        before = overlay.tint
        overlay.cycle_tint()
        self.assertGreater(overlay.tint, before)
        p.toggle_pin()
        self.assertTrue(overlay.windowFlags() & Qt.WindowStaysOnTopHint)
        p.toggle_pin()
        self.assertFalse(overlay.windowFlags() & Qt.WindowStaysOnTopHint)
        self.qt_click(overlay.transcript)
        p.open_knowledge(); self.root.update()
        self.assertEqual(p.layout_mode, 'compact')
        self.assertFalse(overlay.isVisible())
        self.assertTrue(p.pages.active.winfo_ismapped())
        self.assertEqual(float(self.root.attributes('-alpha')), 1)
        self.assertEqual(self.root.attributes('-transparentcolor'), '')
        self.assertEqual(p.controller.session_id, session)
        self.assertEqual(len(self.calls), 1)
        p.set_layout('meeting'); self.qt_click(overlay.listen)
        self.wait(lambda: not p.controller.busy)
        self.wait(lambda: overlay.listen.icon_name == 'play')

    def test_meeting_alerts_are_bounded_and_never_raise_window(self):
        from sentinel_ui import refresh_view
        p = self.panel
        overlay = self.meeting()
        geometry = overlay.geometry()
        with patch.object(overlay, 'activateWindow') as focus, patch.object(overlay, 'show') as show:
            p.handle(dict(kind='alert', headline='预算仍需确认' * 150, summary='确认预算上限', action='询问预算范围'))
            refresh_view(p); p.meeting_overlay.app.processEvents()
            focus.assert_not_called(); show.assert_not_called()
        self.assertIn('预算仍需确认', overlay.headline_value)
        self.assertEqual(overlay.geometry(), geometry)
        self.assertIsNone(overlay.content)
        self.assertFalse(p.unread_alert)
        self.qt_click(overlay.background); p.meeting_overlay.app.processEvents()
        overlay.resize(600, 320)
        p.meeting_overlay.app.processEvents()
        area = overlay.content.reminder
        self.assertIn('预算仍需确认', area.toPlainText())
        area.verticalScrollBar().setValue(50)
        before = area.verticalScrollBar().value()
        refresh_view(p)
        self.assertEqual(area.verticalScrollBar().value(), before)
        p.handle(dict(kind='alert', headline='新的重点提醒' * 150, summary='新卡点', action='先核实'))
        refresh_view(p)
        self.assertEqual(area.verticalScrollBar().value(), 0)
        overlay.content.toggle_sources('background')
        p.meeting_overlay.app.processEvents()
        self.assertLessEqual(overlay.height(), 340)
        self.assertTrue(overlay.mask().contains(overlay.grip.mapTo(overlay, overlay.grip.rect().center())))
        overlay.content.toggle_sources('background')
        p.error.set('长诊断不能挤走控制按钮。' * 100)
        refresh_view(p); p.meeting_overlay.app.processEvents()
        for widget in (overlay.listen, overlay.home, overlay.hide_button, overlay.transcript, overlay.graph, overlay.more, overlay.status):
            self.assertTrue(widget.isVisible())
            self.assertLessEqual(widget.geometry().right(), overlay.width())
        p.hide_window()
        with patch.object(overlay, 'activateWindow') as focus, patch.object(overlay, 'show') as show:
            p.handle(dict(kind='alert', headline='隐藏后的新提醒', summary='测试', action='测试'))
            refresh_view(p); p.meeting_overlay.app.processEvents()
            show.assert_not_called(); focus.assert_not_called()
        self.assertTrue(p.hidden)
        self.assertFalse(p.window_visible())
        self.assertTrue(p.unread_alert)
        p.show_window()
        self.assertTrue(p.window_visible())
        self.assertFalse(any(isinstance(w, tk.Toplevel) for w in self.root.winfo_children()))
        self.assertFalse(self.calls)

    def test_meeting_icons_expand_in_place_and_same_icon_collapses(self):
        p = self.panel
        overlay = self.meeting()
        collapsed = overlay.height()
        p.handle(dict(kind='final', turn_id=1, text='双方同意周五交付', role='对方', start_s=12))
        p.handle(dict(kind='briefing', revision=1, source_epoch=0, briefing=dict(
            situation='交付日已明确', agreements=[], open_questions=[],
            background='讨论交付时间', background_quotes=['双方同意周五交付'])))
        self.qt_click(overlay.transcript); p.meeting_overlay.app.processEvents()
        self.assertEqual(p.layout_mode, 'meeting')
        self.assertGreater(overlay.height(), collapsed*3)
        self.assertLessEqual(overlay.width(), 700)
        self.assertLessEqual(overlay.height(), 340)
        self.assertIn('双方同意周五交付', overlay.content.transcript.toPlainText())
        self.assertIn('对方', overlay.content.transcript.toPlainText())
        self.assertIn('讨论交付时间', overlay.content.context[0].text())
        self.assertIn('交付日已明确', overlay.content.context[1].text())
        self.assertIsNone(p.pages.active)
        with patch.object(p, 'enable_logic_analysis') as enable:
            self.qt_click(overlay.graph); p.meeting_overlay.app.processEvents()
            enable.assert_called_once()
        graph_size = overlay.size()
        self.assertGreater(overlay.height(), 340)
        self.assertGreater(overlay.width(), 700)
        self.assertTrue(overlay.content.graph_view.isVisible())
        self.assertIs(overlay.content.graph_view.doc, p.logic_document)
        self.qt_click(overlay.transcript)
        self.assertLessEqual(overlay.height(), 340)
        self.assertLessEqual(overlay.width(), 700)
        with patch.object(p, 'enable_logic_analysis'):
            self.qt_click(overlay.graph)
        self.assertEqual(overlay.size(), graph_size)
        self.qt_click(overlay.graph); p.meeting_overlay.app.processEvents()
        self.assertIsNone(overlay.view)
        self.assertEqual(overlay.height(), collapsed)
        self.assertFalse(overlay.content.isVisible())
        self.qt_click(overlay.home); self.root.update()
        self.assertEqual(p.layout_mode, 'compact')
        self.assertEqual(float(self.root.attributes('-alpha')), 1)
        self.assertFalse(any(isinstance(w, tk.Toplevel) for w in self.root.winfo_children()))
        self.assertFalse(self.calls)

    def test_meeting_icon_release_outside_cancels_and_keyboard_works(self):
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtTest import QTest
        p = self.panel
        overlay = self.meeting()
        icon = overlay.home
        QTest.mousePress(icon, Qt.LeftButton, pos=QPoint(10, 10))
        QTest.mouseRelease(icon, Qt.LeftButton, pos=QPoint(-10, 10))
        self.assertEqual(p.layout_mode, 'meeting')
        QTest.keyClick(icon, Qt.Key_Return)
        self.wait(lambda: p.layout_mode == 'compact')
        self.assertEqual(p.layout_mode, 'compact')
        self.assertFalse(self.calls)

    def test_meeting_native_failure_uses_opaque_text_and_readable_fallback(self):
        with patch('sentinel_meeting_window.apply_backdrop', return_value=False):
            overlay = self.meeting()
            self.assertFalse(overlay.native_glass)
            self.assertEqual(overlay.windowOpacity(), 1)
            self.assertEqual(self.root.attributes('-transparentcolor'), '')
            self.qt_click(overlay.home); self.root.update()
        self.assertEqual(float(self.root.attributes('-alpha')), 1)
        self.assertFalse(self.calls)

    def test_meeting_geometry_and_preparation_return_to_same_workspace(self):
        p = self.panel
        p.show_window(); self.root.update()
        workspace_position = (self.root.winfo_x(), self.root.winfo_y())
        overlay = self.meeting()
        overlay.move(120, 140)
        position = overlay.pos()
        p.open_preparation(); self.root.update()
        self.assertEqual(p.layout_mode, 'compact')
        self.assertTrue(p.goal_entry.winfo_ismapped())
        self.assertEqual((self.root.winfo_x(), self.root.winfo_y()), workspace_position)
        p.set_layout('meeting')
        self.assertEqual(overlay.pos(), position)
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertFalse(overlay.content)
        from sentinel_ui import refresh_view
        p.unread_alert = True
        refresh_view(p)
        self.assertFalse(p.unread_alert)

    def test_meeting_tray_failure_minimizes_qt_and_toggle_restores(self):
        p = self.panel
        overlay = self.meeting()
        p.hide_window()
        self.assertTrue(p.hidden)
        p.desktop.tray_available = False
        p.recover_taskbar_entry()
        p.meeting_overlay.app.processEvents()
        self.assertFalse(p.hidden)
        self.assertTrue(overlay.isMinimized())
        self.assertEqual(self.root.state(), 'withdrawn')
        p.toggle_window()
        self.assertTrue(p.window_visible())
        p.hide_window(); p.meeting_overlay.app.processEvents()
        self.assertTrue(overlay.isMinimized())
        p.toggle_window()
        self.assertTrue(p.window_visible())
        self.assertFalse(self.calls)

    def test_meeting_sources_clear_after_correction_and_restart(self):
        p = self.panel
        overlay = self.meeting()
        p.start(); self.wait(lambda: p.mic_on)
        p.handle(dict(kind='final', turn_id=1, text='预算是二十万', role='对方', start_s=12))
        p.handle(dict(kind='briefing', revision=1, source_epoch=0, briefing=dict(
            situation='核对预算', agreements=[], open_questions=[],
            background='讨论预算', background_quotes=['预算是二十万'])))
        self.qt_click(overlay.background)
        content = overlay.content
        content.show_sources('background')
        self.assertIn('预算是二十万', content.sources.text())
        p.handle(dict(kind='attribution_update', turn_id=1, text='预算还没确定', role='对方'))
        content.refresh()
        self.assertNotIn('预算是二十万', content.sources.text())
        self.assertNotIn('讨论预算', content.context[0].text())
        self.qt_click(overlay.graph)
        graph = content.graph_view
        raw = p.logic_document.snapshot()['turns'][0]
        graph.inspect(dict(text=raw['text'], evidence=[dict(turn_id=1, quote=raw['text'])]))
        self.assertTrue(graph.detail.isVisible())
        p.stop(); self.wait(lambda: not p.controller.busy)
        p.start(); self.wait(lambda: p.mic_on)
        content.refresh()
        self.assertFalse(graph.detail.isVisible())
        self.assertIs(graph.doc, p.logic_document)
        self.assertNotIn('预算', graph.evidence.toPlainText())

    def test_meeting_graph_relations_keep_sources_and_failure_status(self):
        from sentinel_logic import GraphDocument
        from sentinel_meeting_graph import Edge, EdgeLabel
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        p = self.panel
        overlay = self.meeting()
        doc = p.logic_document = GraphDocument('qt-graph', directory=self.path/'graphs')
        for ident, text in ((1, '预算是二十万'), (2, '报价是二十二万')):
            doc.observe(dict(kind='final', turn_id=ident, text=text, role='对方'))
        nodes = [dict(id='n'+str(i), topic='价格', kind='proposal', title=text, text=text,
                      basis='stated', evidence=[dict(turn_id=i, quote=text)])
                 for i, text in ((1, '预算是二十万'), (2, '报价是二十二万'))]
        doc.apply(dict(nodes=nodes, edges=[dict(source='n1', target='n2', relation='卡住',
                  evidence=[dict(turn_id=2, quote='报价是二十二万')])], reviewed_turn_ids=[1, 2]), doc.turns)
        with patch.object(p, 'enable_logic_analysis'):
            self.qt_click(overlay.graph)
        p.meeting_overlay.app.processEvents()
        graph = overlay.content.graph_view
        self.assertIn('卡住', [item.toPlainText() for item in graph.scene.items() if isinstance(item, EdgeLabel)])
        edge = next(item for item in graph.scene.items() if isinstance(item, Edge))
        self.assertTrue(edge.boundingRect().contains(edge.shape().boundingRect()))
        label = next(child for child in edge.childItems() if isinstance(child, EdgeLabel))
        point = graph.view.mapFromScene(label.sceneBoundingRect().center())
        QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=point)
        self.assertIn('关系：卡住', graph.evidence.toPlainText())
        self.assertIn('报价是二十二万', graph.evidence.toPlainText())
        self.assertTrue(graph.detail.isVisible())
        revision = doc.revision
        doc.status = '逻辑图更新失败，已保留原图：测试错误'
        graph.refresh()
        self.assertEqual(doc.revision, revision)
        self.assertIn('测试错误', graph.status.text())
        with patch.object(p, 'enable_logic_analysis') as retry:
            graph.retry.click()
            retry.assert_called_once()
        # Two distinct relationships joining the same node must remain legible.
        doc.observe(dict(kind='final', turn_id=3, text='先确认方案范围', role='我方'))
        doc.apply(dict(nodes=[dict(id='n3', topic='价格', kind='action', title='我确认方案范围',
                  text='先确认方案范围', evidence=[dict(turn_id=3, quote='先确认方案范围')])],
                  edges=[dict(source=source, target='n3', relation=relation,
                         evidence=[dict(turn_id=3, quote='先确认方案范围')])
                         for source, relation in (('n1', '推进'), ('n2', '条件'))],
                  reviewed_turn_ids=[3]), doc.turns)
        graph.refresh()
        incoming = [item for item in graph.scene.items() if isinstance(item, EdgeLabel)
                    and item.parentItem().edge['target'] == 'n3']
        self.assertEqual(len(incoming), 2)
        self.assertFalse(incoming[0].sceneBoundingRect().intersects(incoming[1].sceneBoundingRect()))
        doc.observe(dict(kind='attribution_update', turn_id=2, text='价格还要确认', role='对方'))
        graph.refresh()
        self.assertFalse(graph.detail.isVisible())
        self.assertEqual(graph.evidence.toPlainText(), '')

    def test_meeting_graph_camera_keeps_top_readable_and_overview_complete(self):
        from sentinel_logic import GraphDocument
        from sentinel_meeting_graph import Node
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        p = self.panel
        overlay = self.meeting()
        doc = p.logic_document = GraphDocument('qt-camera', directory=self.path/'graphs')
        texts = ['我先确认成交条件', '对方预算二十万', '月底交付已确认',
                 '还需采购审批', '报价与预算有差距', '我先确认可调整范围',
                 '我确认审批时间', '条件明确后推进签约']
        nodes = []
        for i, text in enumerate(texts):
            doc.observe(dict(kind='final', turn_id=i+1, text=text, role='对方'))
            nodes.append(dict(id=str(i), topic='成交', kind='proposal', title=text,
                              text=text, basis='stated',
                              evidence=[dict(turn_id=i+1, quote=text)]))
        edges = [dict(source=str(a), target=str(b), relation='推进',
                      evidence=[dict(turn_id=b+1, quote=texts[b])])
                 for a, b in ((0, 1), (0, 2), (0, 3), (1, 4),
                              (4, 5), (3, 6), (5, 7), (6, 7))]
        doc.apply(dict(nodes=nodes, edges=edges, reviewed_turn_ids=list(doc.turns)), doc.turns)
        with patch.object(p, 'enable_logic_analysis'):
            self.qt_click(overlay.graph)
        graph = overlay.content.graph_view
        overlay.resize(600, 320)
        self.wait(lambda: graph.layout_columns == 2 and not graph.camera_timer.isActive())
        self.assertEqual(graph.view.transform().m11(), 1)
        cards = [item for item in graph.scene.items() if isinstance(item, Node)]
        self.assertEqual(len(cards), len(texts))
        first = min(cards, key=lambda item: item.sceneBoundingRect().top())
        visible = graph.view.mapToScene(graph.view.viewport().rect()).boundingRect()
        self.assertTrue(visible.contains(first.sceneBoundingRect()))
        self.assertEqual(graph.view.verticalScrollBar().value(), graph.view.verticalScrollBar().minimum())
        self.assertGreater(graph.view.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(first.title.font().pointSizeF(), 11)

        # One click must show the complete graph, even in the small floating
        # pane. There is no minimum zoom silently cropping distant branches.
        self.qt_click(graph.overview)
        self.wait(lambda: not graph.camera_timer.isActive())
        visible = graph.view.mapToScene(graph.view.viewport().rect()).boundingRect()
        self.assertTrue(all(visible.contains(card.sceneBoundingRect()) for card in cards))
        self.assertLess(graph.view.transform().m11(), .8)
        overview_zoom = graph.view.transform().m11()
        self.qt_click(graph.zoom_in)
        self.wait(lambda: not graph.camera_timer.isActive())
        self.assertGreater(graph.view.transform().m11(), overview_zoom)
        manual_zoom = graph.view.transform().m11()
        revision = doc.revision
        doc.observe(dict(kind='final', turn_id=9, text='还要核对付款条件', role='我方'))
        graph.refresh()
        self.wait(lambda: not graph.camera_timer.isActive())
        self.assertGreater(doc.revision, revision)
        self.assertAlmostEqual(graph.view.transform().m11(), manual_zoom)
        self.assertEqual(len([item for item in graph.scene.items() if isinstance(item, Node)]), 9)

        self.qt_click(graph.overview)
        self.wait(lambda: not graph.camera_timer.isActive())
        card = next(item for item in graph.scene.items()
                    if isinstance(item, Node) and item.node['id'] == '0')
        point = graph.view.mapFromScene(card.sceneBoundingRect().center())
        # Real double clicks begin with a single press/release, which opens
        # evidence and changes the viewport before the double-click event.
        QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=point)
        self.wait(lambda: graph.detail.isVisible() and not graph.camera_timer.isActive())
        QTest.mouseDClick(graph.view.viewport(), Qt.LeftButton, pos=point)
        self.wait(lambda: not graph.camera_timer.isActive())
        self.assertEqual(graph.view.transform().m11(), 1)
        self.assertIn(texts[0], graph.evidence.toPlainText())

        self.qt_click(graph.read_size)
        self.wait(lambda: not graph.camera_timer.isActive())
        self.assertEqual(graph.view.transform().m11(), 1)
        self.assertEqual(graph.view.verticalScrollBar().value(), graph.view.verticalScrollBar().minimum())
        graph.view.verticalScrollBar().setValue(graph.view.verticalScrollBar().maximum())
        before = graph.view.verticalScrollBar().value()
        graph.refresh()
        self.assertEqual(graph.view.verticalScrollBar().value(), before)
        self.assertFalse(self.calls)

    def test_meeting_shutdown_cancels_pump_and_destroys_window(self):
        from shiboken6 import isValid
        overlay = self.meeting()
        bridge = self.panel.meeting_overlay
        self.assertIsNotNone(bridge._pump_id)
        self.root.destroy()
        self.assertIsNone(bridge._pump_id)
        self.assertIsNone(bridge.window)
        self.assertFalse(isValid(overlay))

    def test_meeting_command_failure_does_not_strand_following_input(self):
        self.meeting()
        bridge = self.panel.meeting_overlay
        completed = []
        def fail():
            raise RuntimeError('测试动作失败')
        bridge.dispatch(fail)
        bridge.dispatch(lambda: completed.append('仍可操作'))
        with self.assertLogs(level='ERROR'):
            self.wait(lambda: completed)
        self.assertIsNotNone(bridge._pump_id)
        self.assertIn('测试动作失败', self.panel.error.get())
        bridge.dispatch(self.root.destroy)
        bridge.dispatch(lambda: completed.append('不应执行'))
        bridge.drain_commands()
        self.assertEqual(completed, ['仍可操作'])
        self.assertIsNone(bridge._pump_id)

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
        self.assertFalse(p.goal_editor.winfo_ismapped())
        p.set_layout('compact')
        p.open_preparation(); self.root.update()
        self.assertTrue(p.goal_editor.winfo_ismapped())
        self.assertTrue(p.start_button.winfo_ismapped())
        self.assertTrue(p.stop_button.winfo_ismapped())
        p.set_layout('compact')
        self.assertFalse(p.goal_editor.winfo_ismapped())

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
            if mode=='compact':
                self.assertLessEqual(p.action.winfo_rooty()+p.action.winfo_height(),p.card_shell.winfo_rooty()+p.card_shell.winfo_height())
            if mode=='compact':
                for section in (p.background_view, p.current_view, p.next_goal_view):
                    self.assertTrue(section.winfo_ismapped())
                    self.assertLessEqual(section.winfo_rooty()+section.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())
                self.assertGreater(p.card_shell.winfo_width(),p.context_column.winfo_width())
                self.assertLessEqual(self.root.winfo_height(),round(650*p.ui_scale),'Workspace must stay bounded; claims scroll')

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
        p.set_layout('compact')
        p.close()
        self.assertEqual(p.layout_mode, 'compact')
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
        p.toggle_fold(); self.assertTrue(p.hidden)
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
    def test_hidden_alert_does_not_popup_or_start_audio(self):
        p = self.panel
        self.root.deiconify(); self.root.update()
        p.hide_window()
        with patch.object(self.root, 'deiconify') as show, patch.object(self.root, 'focus_force') as focus:
            p.handle(dict(kind='alert', headline='示例：付款期限待确认', summary='对方提出延期', action='确认日期'))
            self.root.update()
            show.assert_not_called()
            focus.assert_not_called()
        self.assertTrue(p.unread_alert)
        self.assertTrue(p.hidden)
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertFalse(self.calls)
        self.assertFalse(hasattr(p, 'pet'))
        p.show_window(); self.root.update()
        from sentinel_ui import refresh_view
        refresh_view(p)
        self.assertFalse(p.unread_alert)
        self.assertIn('付款期限',p.headline.cget('text'))
        p.open_preparation()
        p.handle(dict(kind='alert', headline='示例：付款期限待确认', summary='对方提出延期', action='确认日期'))
        self.assertTrue(p.unread_alert)
        self.assertTrue(p.preparation.winfo_manager())

    def test_visible_workspace_alert_is_already_read(self):
        p = self.panel
        self.root.deiconify(); p.set_layout('compact'); self.root.update()
        p.handle(dict(kind='alert', headline='已在侧栏显示', action='核对条件'))
        self.assertFalse(p.unread_alert)
        p.close()
        self.assertFalse(p.unread_alert)

    def test_new_alert_returns_to_top_and_transcript_navigation_is_stable(self):
        p = self.panel
        p.show_window(); self.root.update()
        p.handle(dict(kind='alert', headline='长提醒', summary='依据内容。'*600, action='核对条件。'*200))
        self.root.update()
        p.advice_canvas.yview_moveto(1)
        self.assertGreater(p.advice_canvas.yview()[0], 0)
        p.handle(dict(kind='alert', headline='新提醒在顶部', summary='新依据。'*600, action='新回应'))
        self.root.update()
        self.assertEqual(p.advice_canvas.yview()[0], 0)
        p.expand_button.invoke(); self.root.update()
        p.open_desktop_settings(); self.root.update()
        p.expand_button.invoke(); self.root.update()
        self.assertEqual(p.layout_mode, 'detail')
        self.assertIsNone(p.pages.active)
        self.assertTrue(p.transcript.winfo_ismapped())

    def test_taskbar_fallback_restores_without_stale_hidden_state(self):
        p = self.panel
        p.desktop.tray_available = False
        p.show_window(); self.root.update()
        p.hide_window(); self.root.update()
        self.assertFalse(p.hidden)
        self.assertEqual(self.root.state(), 'iconic')
        self.root.deiconify(); self.root.update()
        p.handle(dict(kind='alert', headline='任务栏恢复后已可见', action='核对'))
        self.assertFalse(p.unread_alert)
        p.toggle_fold(); self.root.update()
        self.assertEqual(self.root.state(), 'iconic')

    def test_sidebar_source_entry_remains_reachable_and_context_visible(self):
        p = self.panel
        p.show_window(); self.root.update()
        p.handle(dict(kind='final', turn_id=1, text='双方同意周五交付，付款待定。'))
        p.handle(dict(kind='briefing', revision=1, briefing=dict(
            situation='周五交付已确认，付款待定', background='正在协商交付与付款',
            background_quotes=['周五交付，付款待定'],
            agreements=[dict(text='双方确认周五交付',quotes=['双方同意周五交付'])],
            open_questions=[dict(text='付款条件待确认',quotes=['付款待定'])],
            next_goal='明确付款条件', next_goal_quotes=['付款待定'])))
        from sentinel_ui import refresh_view
        refresh_view(p); self.root.update()
        for section, content in ((p.background_view, '协商交付'), (p.current_view, '已达成'), (p.next_goal_view, '明确付款条件')):
            self.assertTrue(section.winfo_ismapped())
            self.assertIn(content, section.text.get('1.0','end'))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        source = next(w for w in descendants(p.current_view) if isinstance(w,tk.Button) and w.cget('text')=='依据')
        source.invoke(); self.root.update()
        self.assertIn('双方同意周五交付', p.sources_text.get('1.0','end'))
        self.assertFalse(self.calls)

    def test_tray_failure_keeps_taskbar_entry_without_focus_or_stopping_session(self):
        p = self.panel
        p.show_window(); p.start(); self.wait(lambda: p.mic_on)
        session = p.controller.session_id
        for event in (dict(kind='desktop_error',text='注册失败'), dict(kind='tray_ready',available=False), dict(kind='desktop_closed')):
            p.desktop.tray_available = True
            p.hide_window()
            p.desktop.tray_available = False
            with patch.object(self.root, 'focus_force') as focus, patch.object(self.root, 'deiconify') as show:
                p.handle_desktop(event)
                self.root.update()
                focus.assert_not_called()
                show.assert_not_called()
            self.assertEqual(self.root.state(), 'iconic')
            self.assertTrue(p.mic_on)
            self.assertEqual(p.controller.session_id, session)
            self.assertFalse(p.controller.stop_event.is_set())
            p.show_window()

    def test_briefing_sources_and_correction_epoch_survive_views(self):
        p = self.panel
        p.handle(dict(kind='final', turn_id=1, text='双方同意周五交付'))
        brief = dict(situation='已确认交付日，付款待定',
                     agreements=[dict(text='周五交付', quotes=['双方同意周五交付'])], open_questions=[])
        p.handle(dict(kind='briefing', briefing=brief, revision=2, source_epoch=0))
        from sentinel_ui import refresh_view
        refresh_view(p)
        self.assertIn('周五交付', p.current_view.text.get('1.0', 'end'))
        self.assertIn('等待', p.background_view.text.get('1.0', 'end'))
        p.set_layout('detail'); p.close()
        p.handle(dict(kind='speaker_roles', speakers={}, source_epoch=1))
        p.handle(dict(kind='briefing', briefing=brief, revision=99, source_epoch=0))
        p.handle(dict(kind='alert', headline='过期提醒', revision=99, source_epoch=0))
        refresh_view(p)
        self.assertFalse(p.briefing.snapshot()['agreements'])
        self.assertNotEqual(p.headline.cget('text'), '过期提醒')
        p.start(); self.wait(lambda:p.mic_on)
        self.assertFalse(p.briefing.snapshot()['agreements'])
        self.assertFalse(p.unread_alert)

    def test_knowledge_selection_is_separate_from_session_analysis(self):
        from sentinel_knowledge import KnowledgeCatalog
        p = self.panel
        p.knowledge_catalog = KnowledgeCatalog(self.path/'catalog.json')
        p.knowledge_window.catalog = p.knowledge_catalog
        p.open_knowledge()
        entry = p.knowledge_catalog.add_directory(self.path)
        p.refresh_knowledge()
        self.assertIn('检索待接入', p.knowledge_summary.get())
        p.knowledge_window.toggle(entry['pack_id'], tk.BooleanVar(value=False))
        self.assertEqual(p.knowledge_summary.get(), '未选择知识库')
        self.assertFalse(self.calls)
        p.hide_window()
        self.assertEqual(p.knowledge_window.window.state(), 'withdrawn')

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

    def test_returning_from_page_releases_hidden_input_focus_and_keeps_draft(self):
        p = self.panel
        p.quick_note(); self.root.update()
        p.notes.editor.insert('1.0', '保留草稿')
        p.notes.editor.focus_force(); self.root.update()
        p.pages.hide(); self.root.update()
        self.assertIsNot(self.root.focus_get(), p.notes.editor)
        self.assertEqual(p.notes.editor.get('1.0', 'end-1c'), '保留草稿')
        self.assertEqual(p.note_store.read()[-1]['text'], '保留草稿')
        p.quick_note(); self.root.update()
        self.assertEqual(p.notes.editor.get('1.0', 'end-1c'), '保留草稿')

    def test_page_save_failure_cancels_navigation_and_folding(self):
        p = self.panel
        p.quick_note(); self.root.update()
        p.notes.editor.insert('1.0', '不能丢失的草稿')
        active = p.pages.active
        with patch.object(p.note_store, 'save', side_effect=OSError('测试：存储不可用')):
            self.assertFalse(p.pages.hide())
            self.assertIs(p.pages.active, active)
            p.hide_window()
            self.assertFalse(p.hidden)
            self.assertEqual(p.layout_mode, 'compact')
            self.assertIs(p.pages.active, active)
        self.assertTrue(p.pages.hide())
        self.assertEqual(p.note_store.read()[-1]['text'], '不能丢失的草稿')

    def test_old_always_on_top_default_is_migrated_but_manual_choice_persists(self):
        path = self.path/'legacy-settings.json'
        path.write_text(json.dumps(dict(version=1, topmost=True, hotkeys={})), encoding='utf-8')
        settings = DesktopPreferences(path)
        self.assertFalse(settings.value['topmost'])
        settings.save(topmost=True)
        self.assertTrue(DesktopPreferences(path).value['topmost'])
        settings.save(topmost=False)
        self.assertFalse(DesktopPreferences(path).value['topmost'])

    def test_graph_gives_canvas_the_window_and_restores_workspace_on_return(self):
        p = self.panel
        self.root.deiconify(); p.set_layout('compact'); self.root.update()
        with patch.object(p, 'enable_logic_analysis'):
            p.open_logic(); self.root.update()
        view=p.logic_window
        for width,height in ((900,620),(800,560)):
            self.root.geometry(f'{round(width*p.ui_scale)}x{round(height*p.ui_scale)}')
            self.root.update()
            self.assertGreater(view.canvas.winfo_height()/self.root.winfo_height(), .70)
            self.assertGreater(view.canvas.winfo_width()/self.root.winfo_width(), .94)
            self.assertFalse(view.drawer.winfo_ismapped())
            self.assertEqual(float(self.root.attributes('-alpha')),1)
        self.assertEqual(p.goal_summary.winfo_manager(),'')
        self.assertTrue(p.toolbar.winfo_ismapped())
        self.assertTrue(p.start_button.winfo_ismapped())
        self.assertTrue(p.stop_button.winfo_ismapped())
        view.toggle_inspector();self.root.update()
        self.assertTrue(view.drawer.winfo_ismapped())
        view.hide_inspector();self.root.update()
        self.assertFalse(view.drawer.winfo_ismapped())
        view.window.withdraw();self.root.update()
        self.assertIsNone(p.pages.active)
        self.assertTrue(p.expand_button.winfo_ismapped())
        self.assertFalse(p.goal_summary.winfo_ismapped())
        self.assertTrue(p.toolbar.winfo_ismapped())
        self.assertFalse(self.calls)
        p.set_layout('detail');self.root.update()
        with patch.object(p, 'enable_logic_analysis'):
            p.open_logic();self.root.update()
        view.window.withdraw();self.root.update()
        self.assertTrue(p.detail.winfo_ismapped())
        self.assertFalse(p.goal_summary.winfo_ismapped())

    def test_tools_share_workspace_and_default_is_not_topmost(self):
        p = self.panel
        self.assertFalse(p.topmost.get())
        self.assertFalse(bool(self.root.attributes('-topmost')))
        self.root.deiconify()
        def native_windows(widget):
            return sum(isinstance(c, tk.Toplevel) + native_windows(c) for c in widget.winfo_children())
        for action in (p.open_desktop_settings, p.quick_note, p.open_knowledge, p.open_speakers, p.open_api_settings):
            action(); self.root.update()
            self.assertIsNotNone(p.pages.active)
            self.assertTrue(p.pages.active.winfo_ismapped())
            self.assertEqual(native_windows(self.root), 0)
            self.assertFalse(self.calls)
        self.assertFalse(p.topmost.get())
        p.quick_note(); self.root.update()
        self.assertTrue(p.notes.save_button.winfo_ismapped())
        p.notes.editor.insert('1.0', '切换页面保留的草稿')
        p.open_knowledge(); p.quick_note()
        self.assertIn('保留的草稿', p.notes.editor.get('1.0', 'end'))
        p.pages.hide(); self.root.update()
        self.assertIsNone(p.pages.active)
        p.show_sources(dict(text='示例归纳', quotes=['来源原话']))
        self.assertIn('来源原话', p.sources_text.get('1.0', 'end'))
        self.assertEqual(native_windows(self.root), 0)
        with patch.object(p, 'enable_logic_analysis'):
            p.open_logic(); self.root.update()
        self.assertTrue(p.logic_window.window.winfo_ismapped())
        self.assertEqual(native_windows(self.root), 0)
        p.close(); self.assertTrue(p.hidden)



if __name__ == '__main__': unittest.main()
