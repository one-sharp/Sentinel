"""Session lifecycle contracts without Tk, real audio, models or network calls."""
import queue
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sentinel_session import SessionController


class SessionControllerTests(unittest.TestCase):
    def controller(self, runner):
        controller = SessionController(SimpleNamespace(goal='test goal'), runner)

        def cleanup():
            controller.stop()
            if controller.thread is not None:
                controller.thread.join(timeout=3)
                self.assertFalse(controller.thread.is_alive())

        self.addCleanup(cleanup)
        return controller

    def join(self, controller):
        controller.thread.join(timeout=3)
        self.assertFalse(controller.thread.is_alive())

    def test_duplicate_start_and_stop_wait_for_worker_cleanup(self):
        entered, release = threading.Event(), threading.Event()

        def runner(args, on_event, stop_event):
            entered.set()
            stop_event.wait(3)
            release.wait(3)

        controller = self.controller(runner)
        # Unblock the runner before controller cleanup even on assertion failure.
        self.addCleanup(release.set)
        self.assertTrue(controller.start('meeting'))
        self.assertTrue(entered.wait(3))
        self.assertFalse(controller.start('dispute'))
        controller.stop()
        self.assertTrue(controller.busy)
        self.assertFalse(controller.reap())
        release.set()
        self.join(controller)
        self.assertTrue(controller.reap())
        self.assertFalse(controller.busy)
        self.assertFalse(controller.reap())

    def test_last_events_are_consumed_before_restart(self):
        def runner(args, on_event, stop_event):
            on_event(kind='final', text=args.scene)
            on_event(kind='stopped')

        controller = self.controller(runner)
        controller.start('meeting')
        self.join(controller)
        self.assertTrue(controller.busy)
        self.assertFalse(controller.reap())
        self.assertFalse(controller.start('dispute'))
        self.assertEqual(controller.events.get_nowait(), {'kind': 'final', 'text': 'meeting'})
        self.assertFalse(controller.reap())
        self.assertEqual(controller.events.get_nowait(), {'kind': 'stopped'})
        self.assertTrue(controller.reap())
        controller.start('dispute')
        self.join(controller)
        self.assertEqual(controller.events.get_nowait(), {'kind': 'final', 'text': 'dispute'})
        self.assertEqual(controller.events.get_nowait(), {'kind': 'stopped'})

    def test_restart_has_independent_arguments_stop_event_and_commands(self):
        sessions = []

        def runner(args, on_event, stop_event):
            sessions.append((args, stop_event))
            on_event(kind='command', command=args.controls.get(timeout=3))
            stop_event.wait(3)

        controller = self.controller(runner)
        controller.start('meeting')
        first_id = controller.session_id
        controller.send(kind='role', speaker_id='Speaker B', role='对方')
        controller.stop()
        self.join(controller)
        self.assertEqual(controller.events.get_nowait()['command']['kind'], 'role')
        self.assertTrue(controller.reap())
        controller.send(kind='must_not_reach_next_session')
        controller.start('dispute', enrollment=True)
        self.assertNotEqual(controller.session_id, first_id)
        self.assertFalse(controller.stop_event.is_set())
        controller.send(kind='finish_enrollment')
        controller.stop()
        self.join(controller)
        self.assertEqual(controller.events.get_nowait()['command'], {'kind': 'finish_enrollment'})
        first, second = sessions
        self.assertIsNot(first[0], second[0])
        self.assertIsNot(first[0].controls, second[0].controls)
        self.assertIsNot(first[1], second[1])
        self.assertEqual((first[0].scene, first[0].enroll_only), ('meeting', False))
        self.assertEqual((second[0].scene, second[0].enroll_only), ('dispute', True))
        self.assertEqual(vars(controller.args), {'goal': 'test goal'})

    def test_runner_failure_is_reported_and_can_restart(self):
        def runner(args, on_event, stop_event):
            if args.scene == 'meeting':
                raise RuntimeError('model failed')

        controller = self.controller(runner)
        with self.assertLogs(level='ERROR'):
            controller.start('meeting')
            self.join(controller)
        self.assertFalse(controller.reap())
        self.assertEqual(controller.events.get_nowait(), {'kind': 'fatal', 'text': 'model failed'})
        self.assertTrue(controller.reap())
        self.assertTrue(controller.start('general'))
        self.join(controller)
        self.assertTrue(controller.reap())

    def test_thread_start_failure_allows_retry(self):
        controller = self.controller(lambda *args, **kwargs: None)
        with patch('sentinel_session.threading.Thread.start', side_effect=RuntimeError('cannot start')):
            with self.assertRaisesRegex(RuntimeError, 'cannot start'):
                controller.start('meeting')
        self.assertFalse(controller.busy)
        self.assertTrue(controller.start('general'))
        self.join(controller)
        self.assertTrue(controller.reap())

    def test_idle_stop_and_commands_do_not_create_work(self):
        controller = self.controller(lambda *args, **kwargs: self.fail('must remain idle'))
        controller.stop()
        controller.send(kind='finish_enrollment')
        self.assertFalse(controller.busy)
        self.assertFalse(controller.reap())
        with self.assertRaises(queue.Empty):
            controller.events.get_nowait()

    def test_controller_runs_when_gui_audio_and_sdk_imports_are_unavailable(self):
        code = '''
import importlib.abc
import sys
from types import SimpleNamespace
class BlockOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'tkinter', 'sounddevice', 'sherpa_onnx', 'openai'}:
            raise ImportError('Optional dependency must not be loaded: ' + fullname)
sys.meta_path.insert(0, BlockOptionalImports())
from sentinel_session import SessionController
c = SessionController(SimpleNamespace(), lambda *a, **k: None)
assert c.start('meeting')
c.thread.join(3)
assert c.reap()
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parent,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
