"""Lifecycle regression tests; no real microphone or paid API is used."""
import contextlib
import gc
import io
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sentinel_live import create_parser, run
from sentinel_observer import LLMObserver
from sentinel_panel import SentinelPanel
from test_observer import FakeChat, MemoryLog


class DesktopTests(unittest.TestCase):
    def setUp(self):
        # Tk objects must also be garbage-collected on the creating thread.
        gc.collect()
        gc.disable()
        self.root = tk.Tk()
        self.root.withdraw()
        self.panels = []

    def tearDown(self):
        for panel in self.panels:
            panel.controller.stop()
            if panel.controller.thread:
                panel.controller.thread.join(timeout=3)
                self.assertFalse(panel.controller.thread.is_alive())
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.root.report_callback_exception = None
        self.panels.clear()
        self.root = None
        gc.collect()
        gc.enable()

    def panel(self, runner):
        args = create_parser().parse_args(["--llm"])
        panel = SentinelPanel(self.root, args, runner)
        self.panels.append(panel)
        return panel

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Timed out waiting for desktop lifecycle")

    def test_idle_duplicate_start_stop_restart_and_scene(self):
        calls = []

        def runner(args, on_event, stop_event):
            calls.append((args.scene, args.duration, stop_event))
            on_event(kind="listening")
            stop_event.wait(3)

        panel = self.panel(runner)
        self.root.update()
        self.assertEqual(calls, [])
        self.assertIn("待机", panel.state.get())
        panel.start_button.invoke()
        panel.start()  # Even direct repeated calls cannot bypass the guard.
        self.wait_until(lambda: "监听中" in panel.state.get())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ("negotiation", 0))
        panel.stop_button.invoke()
        self.wait_until(lambda: not panel.controller.busy)
        panel.scene.set("会议")
        panel.start_button.invoke()
        self.wait_until(lambda: len(calls) == 2)
        self.assertEqual(calls[1][0], "meeting")
        self.assertIsNot(calls[0][2], calls[1][2])
        self.assertFalse(calls[1][2].is_set())
        panel.stop()
        self.wait_until(lambda: not panel.controller.busy)

    def test_error_remains_visible_after_worker_exit_and_can_restart(self):
        def runner(args, on_event, stop_event):
            raise RuntimeError("test microphone unavailable")

        panel = self.panel(runner)
        with self.assertLogs(level="ERROR"):
            panel.start()
            self.wait_until(lambda: not panel.controller.busy)
        self.assertIn("microphone unavailable", panel.error.get())
        self.assertIn("存在错误", panel.state.get())
        self.assertEqual(str(panel.start_button["state"]), "normal")

    def test_close_waits_for_cleanup_without_blocking_tk(self):
        entered, release = threading.Event(), threading.Event()

        def runner(args, on_event, stop_event):
            entered.set()
            stop_event.wait(3)
            release.wait(3)

        panel = self.panel(runner)
        panel.start()
        self.assertTrue(entered.wait(1))
        panel.request_exit()
        try:
            self.root.update()
            self.assertTrue(self.root.winfo_exists())
            self.assertTrue(panel.controller.busy)
            self.assertTrue(panel.controller.stop_event.is_set())
            self.assertEqual(str(panel.start_button["state"]), "disabled")
        finally:
            release.set()
        self.wait_until(lambda: not panel.controller.busy)
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()

    def test_enrollment_does_not_start_without_consent(self):
        calls = []
        panel = self.panel(lambda *a, **k: calls.append(a))
        panel.enroll()
        self.assertEqual(calls, [])
        self.assertFalse(panel.controller.busy)
        self.assertIn("同意", panel.error.get())
        self.assertFalse(panel.clip_consent.get())

    def test_attribution_update_keeps_original_text_in_ui(self):
        panel = self.panel(lambda *a, **k: None)
        panel.handle(dict(kind="final", turn_id=1, text="没有同意", start_s=1, end_s=3,
                          speaker_id="unknown", role="未绑定", confidence=0))
        panel.handle(dict(kind="attribution_update", turn_id=1, text="没有同意", start_s=1, end_s=3,
                          speaker_id="Speaker B", role="对方", confidence=.8))
        shown = panel.transcript.get("1.0", "end")
        self.assertIn("Speaker B / 对方", shown)
        self.assertIn("没有同意", shown)


class ShutdownTests(unittest.TestCase):
    def test_stop_waits_for_active_llm_cancels_pending_and_suppresses_alert(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        log, events = MemoryLog(), []

        class BlockingChat(FakeChat):
            def create(self, **kwargs):
                entered.set()
                release.wait(4)
                return super().create(**kwargs)

        fake = BlockingChat(delay=0)
        client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        ob = LLMObserver(log, "negotiation", client=client, on_event=lambda **evt: events.append(evt))
        stopper = threading.Thread(target=lambda: (ob.stop(), finished.set()))
        try:
            ob.submit("", "原定三十天付款，对方要求六十天")
            self.assertTrue(entered.wait(1))
            ob.submit("", "原定三十天付款，对方要求六十天，但尚未同意", force=True)
            stopper.start()
            self.assertTrue(ob.stop_flag.wait(1))
            self.assertFalse(finished.wait(.1))
            self.assertFalse(ob.submit("", "停止后不能再分析任何新的会话文本", force=True))
        finally:
            release.set()
            stopper.join(3)
            ob.stop()
        self.assertTrue(finished.is_set())
        self.assertFalse(ob.thread.is_alive())
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(any(e["kind"] == "alert" for e in events))
        self.assertTrue(any(kind == "llm_cancelled" for kind, _ in log.rows))

    def test_owned_client_closes_after_worker_stops(self):
        client = SimpleNamespace(close=lambda: None)
        from unittest.mock import Mock
        client.close = Mock()
        with patch("sentinel_observer.create_client", return_value=(client, "test")):
            ob = LLMObserver(MemoryLog(), "general")
        ob.stop()
        ob.stop()
        client.close.assert_called_once()
        self.assertFalse(ob.thread.is_alive())


class RunLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.log_patch = patch("sentinel_live.logs_dir", return_value=Path("logs"))
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        project = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(dir=project, prefix=".sentinel-test-")
        # Validate the recursive-cleanup target remains inside this workspace.
        self.assertEqual(Path(self.temp.name).resolve().parent, project)
        self.old_cwd = Path.cwd()
        os.chdir(self.temp.name)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        os.chdir(self.old_cwd)
        self.temp.cleanup()

    def rows(self):
        return [json.loads(line) for line in next(Path("logs").glob("session-*.jsonl")).read_text(encoding="utf-8").splitlines()]

    def test_stop_during_model_loading_never_opens_microphone_or_llm(self):
        stop = threading.Event()

        def load(*args):
            stop.set()
            return object()

        with patch("sentinel_live.build_recognizer", side_effect=load), \
                patch("sounddevice.InputStream") as mic, patch("sentinel_live.LLMObserver") as observer:
            run(create_parser().parse_args(["--llm"]), stop_event=stop)
        mic.assert_not_called()
        observer.assert_not_called()
        self.assertEqual(self.rows()[-1]["type"], "summary")

    def test_model_failure_is_logged_with_summary(self):
        with patch("sentinel_live.build_recognizer", side_effect=RuntimeError("model failed")):
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                run(create_parser().parse_args([]))
        rows = self.rows()
        self.assertTrue(any(row["type"] == "error" and "model failed" in row["error"] for row in rows))
        self.assertEqual(rows[-1]["type"], "summary")

    def test_microphone_closes_before_observer_and_log_closes_before_stopped(self):
        stop, order = threading.Event(), []

        class Mic:
            def __enter__(self):
                order.append("mic_open")
                stop.set()

            def __exit__(self, *args):
                order.append("mic_close")

        class Observer:
            requests = failures = completed = alerts = coalesced = 0
            last_latency_s = None

            def __init__(self, log, *args, **kwargs):
                self.log = log

            def stop(self):
                order.append("observer_stop")
                self.log.write("test_observer_stopped")

        def event(**evt):
            if evt["kind"] == "stopped":
                # Exclusive open checks Windows handles were actually released.
                path = Path(evt["log_path"])
                moved = path.with_suffix(".check")
                path.rename(moved)
                moved.rename(path)
                order.append("stopped")

        recognizer = SimpleNamespace(create_stream=lambda: object())
        with patch("sentinel_live.build_recognizer", return_value=recognizer), \
                patch("sounddevice.InputStream", return_value=Mic()), \
                patch("sentinel_live.LLMObserver", Observer):
            run(create_parser().parse_args(["--llm"]), on_event=event, stop_event=stop)
        self.assertEqual(order, ["mic_open", "mic_close", "observer_stop", "stopped"])
        self.assertEqual(self.rows()[-1]["type"], "summary")

    def test_repeated_sessions_do_not_overwrite_logs(self):
        stop = threading.Event()
        stop.set()
        args = create_parser().parse_args([])
        run(args, stop_event=stop)
        run(args, stop_event=stop)
        self.assertEqual(len(list(Path("logs").glob("session-*.jsonl"))), 2)

    def test_one_microphone_fans_out_identical_samples_and_capture_clock(self):
        import numpy as np
        from sentinel_speakers import SpeakerTimeline
        stop = threading.Event()
        accepted, fed, order = [], [], []

        class Worker:
            def __init__(self, *args, **kwargs):
                self.ready = threading.Event()
                self.ready.set()
                self.failure = None
                self.timeline = SpeakerTimeline()
            def feed(self, samples, rate, start, discontinuity):
                fed.append((samples, rate, start))
            def stats(self):
                return {}
            def stop(self):
                order.append("speaker_stop")

        def accept(rate, samples):
            accepted.append(samples)
            stop.set()

        class Mic:
            def __init__(self, **kwargs):
                self.callback = kwargs['callback']
            def __enter__(self):
                self.callback(np.ones((4800, 1), np.float32), 4800,
                              SimpleNamespace(inputBufferAdcTime=0), False)
                self.callback(np.ones((4800, 1), np.float32), 4800,
                              SimpleNamespace(inputBufferAdcTime=.1), False)
            def __exit__(self, *args):
                order.append("mic_close")

        recognizer = SimpleNamespace(create_stream=lambda: SimpleNamespace(accept_waveform=accept),
                                     is_ready=lambda stream: False)
        args = create_parser().parse_args(["--speakers"])
        with patch("sounddevice.InputStream", side_effect=Mic) as mic, \
                patch("sentinel_live.build_recognizer", return_value=recognizer), \
                patch("sentinel_speakers.SpeakerWorker", Worker):
            run(args, stop_event=stop)
        self.assertEqual(mic.call_count, 1)
        self.assertIs(accepted[0], fed[0][0])
        self.assertEqual(fed[0][1:], (48000, 0.))
        self.assertAlmostEqual(fed[1][2], .1)
        self.assertEqual(order, ["mic_close", "speaker_stop"])


if __name__ == "__main__":
    unittest.main()
