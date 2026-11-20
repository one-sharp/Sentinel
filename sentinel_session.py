"""Session worker ownership, independent of Tk and audio/model dependencies.

The desktop thread owns start/stop/send/reap and consumes ``events``. ``busy``
stays true until the worker exits AND its queued events have been consumed,
so a restarted session cannot receive the previous session's queued results.
The runner receives a fresh argument copy, control queue and stop event each time.
"""
from __future__ import annotations

import copy
import logging
import queue
import threading


class SessionController:
    """Own one complete ASR + LLM session until its worker has actually exited."""

    def __init__(self, args, runner):
        self.args, self.runner = args, runner
        self.events = queue.Queue()
        self.thread = None
        self.stop_event = None
        self.session_id = None

    @property
    def busy(self):
        return self.thread is not None

    def start(self, scene, enrollment=False):
        if self.busy:
            return False
        args = copy.copy(self.args)
        from sentinel_desktop import new_session_id
        self.session_id = new_session_id()
        args.scene = scene
        args.enroll_only = enrollment
        args.controls = queue.Queue()
        self.controls = args.controls
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._worker, args=(args, self.stop_event),
                                       daemon=False, name="sentinel-asr")
        try:
            self.thread.start()
        except Exception:
            self.thread = None
            raise
        return True

    def _worker(self, args, stop_event):
        try:
            self.runner(args, on_event=lambda **evt: self.events.put(evt), stop_event=stop_event)
        except Exception as exc:
            logging.exception("Sentinel session failed")
            self.events.put({"kind": "fatal", "text": str(exc)})

    def stop(self):
        if self.stop_event is not None:
            self.stop_event.set()

    def send(self, **command):
        if self.busy:
            self.controls.put(command)

    def reap(self):
        # Drain callbacks before allowing a new session to receive events.
        if self.thread is not None and not self.thread.is_alive() and self.events.empty():
            self.thread.join()
            self.thread = None
            return True
        return False
