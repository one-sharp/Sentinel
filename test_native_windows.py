"""Opt-in real Windows test. Never opens a microphone or calls an LLM."""
from datetime import datetime
import json
import os
from pathlib import Path
import time
import unittest

from sentinel_desktop import ROOT
from sentinel_windows import WindowsDesktop, WM_HOTKEY, WM_TRAY, HOTKEY_IDS, InstanceGuard, MESSAGE_WINDOW
import uuid


@unittest.skipUnless(os.name == 'nt' and os.getenv('SENTINEL_NATIVE_TESTS') == '1',
                     'Set SENTINEL_NATIVE_TESTS=1 outside the sandbox for native registration tests')
class NativeWindowsTests(unittest.TestCase):
    def test_single_instance_mutex_is_released(self):
        key = 'test-' + uuid.uuid4().hex
        first, second, third = InstanceGuard(key), InstanceGuard(key), InstanceGuard(key)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(third.acquire())
        finally:
            first.release(); second.release(); third.release()

    def test_real_registration_conflict_dispatch_tray_and_unregistration(self):
        mapping = dict(zip(HOTKEY_IDS, ('Ctrl+Alt+Shift+F17','Ctrl+Alt+Shift+F18',
                                       'Ctrl+Alt+Shift+F19','Ctrl+Alt+Shift+F20')))
        services, all_events = [], []
        def create():
            events = []
            service = WindowsDesktop(lambda **e:events.append(e), hotkeys=mapping)
            services.append(service)
            all_events.append(events)
            service.start()
            self.assertTrue(service.ready.wait(8))
            self.assertTrue(service.tray_available, events)
            return service, events
        try:
            first, events = create()
            self.assertTrue(next(e for e in events if e['kind']=='hotkeys_applied')['success'], events)
            first.user.PostMessageW(first.hwnd, WM_HOTKEY, HOTKEY_IDS['quick_note'], 0)
            deadline = time.monotonic()+2
            while time.monotonic()<deadline and not any(e.get('action')=='quick_note' for e in events):
                time.sleep(.01)
            self.assertTrue(any(e.get('action')=='quick_note' for e in events))
            show_message = first.user.RegisterWindowMessageW(MESSAGE_WINDOW + '.show')
            first.user.PostMessageW(first.hwnd, show_message, 0, 0)
            deadline = time.monotonic()+2
            while time.monotonic()<deadline and not any(e.get('action')=='show_window' for e in events):
                time.sleep(.01)
            self.assertTrue(any(e.get('action')=='show_window' for e in events))
            # Create/destroy the real native tray menu, select Exit without
            # injecting input into the user's desktop or leaving a blocking menu.
            track_menu = first.user.TrackPopupMenu
            try:
                first.user.TrackPopupMenu = lambda *args: 104
                first.user.PostMessageW(first.hwnd, WM_TRAY, 0, 0x205)
                deadline = time.monotonic()+2
                while time.monotonic()<deadline and not any(e.get('action')=='exit' for e in events):
                    time.sleep(.01)
                self.assertTrue(any(e.get('action')=='exit' for e in events), events)
            finally:
                first.user.TrackPopupMenu = track_menu
            second, conflicts = create()
            result = next(e for e in conflicts if e['kind']=='hotkeys_applied')
            self.assertFalse(result['success'])
            self.assertEqual(len(result['errors']), 4)
            first.close(); first.thread.join(5)
            self.assertTrue(first.closed.is_set())
            self.assertFalse(first.thread.is_alive())
            third, restored = create()
            self.assertTrue(next(e for e in restored if e['kind']=='hotkeys_applied')['success'], restored)
            updated = mapping | {'quick_note':'Ctrl+Alt+Shift+F21'}
            third.configure(updated)
            deadline = time.monotonic()+2
            while time.monotonic()<deadline and len([e for e in restored if e['kind']=='hotkeys_applied']) < 2:
                time.sleep(.01)
            applied = [e for e in restored if e['kind']=='hotkeys_applied'][-1]
            self.assertTrue(applied['success'])
            self.assertEqual(applied['active']['quick_note'], updated['quick_note'])
        finally:
            for service in services:
                service.close()
            for service in services:
                service.thread.join(5)
                self.assertFalse(service.thread.is_alive())
                self.assertFalse(service.tray_available)
            evidence = ROOT/'logs'/('native-desktop-test-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
            evidence.parent.mkdir(exist_ok=True)
            evidence.write_text(json.dumps({'events':all_events, 'all_threads_closed':all(s.closed.is_set() for s in services)},
                                            ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__': unittest.main()
