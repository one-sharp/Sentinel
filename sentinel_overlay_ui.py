"""Lazy Qt presentation bridge; Tk remains the only session/event-loop owner.

Only the explicit show path activates a window. The 16 ms Tk timer pumps Qt
on the same UI thread; neither toolkit may start another blocking event loop.
"""
import tkinter as tk
from collections import deque


class MeetingOverlay(tk.Frame):
    def __init__(self, panel):
        super().__init__(panel.shell)
        self.panel = panel
        self.window = self.app = None
        self._pump_id = None
        self._disposed = False
        self._commands = deque()
        panel.root.bind('<Destroy>', self._on_destroy, add='+')

    def ensure_window(self):
        if self.window is None:
            from PySide6.QtWidgets import QApplication
            from sentinel_meeting_window import MeetingWindow
            self.app = QApplication.instance() or QApplication(['Sentinel'])
            self.app.setQuitOnLastWindowClosed(False)
            self.window = MeetingWindow(self.panel, self.dispatch)
            self._pump()
        return self.window

    def _pump(self):
        self._pump_id = None
        if self._disposed:
            return
        try:
            self.app.processEvents()
            self.drain_commands()
        finally:
            # A failed application action must not strand all future Qt input.
            if not self._disposed:
                self._pump_id = self.panel.root.after(16, self._pump)

    def dispatch(self, command):
        """Qt native callbacks must never enter Tcl from its Windows loop.

        Tk can dispatch Qt HWND messages while its own interpreter is outside
        Python. Queue application actions until the next Python-owned Tk tick.
        """
        if not self._disposed:
            self._commands.append(command)

    def drain_commands(self):
        for _ in range(len(self._commands)):
            if self._disposed or not self._commands:
                return
            self._commands.popleft()()

    def show(self):
        self.ensure_window().show_explicit()

    def hide(self):
        if self.window:
            self.window.hide()

    def minimize(self):
        self.ensure_window().showMinimized()

    def is_visible(self):
        return bool(self.window and self.window.isVisible() and not self.window.isMinimized())

    def refresh(self):
        if self.window and self.is_visible():
            self.window.refresh()

    def update_pin(self):
        if self.window:
            self.window.update_pin()

    def _on_destroy(self, event):
        if event.widget is self.panel.root:
            self.dispose()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._commands.clear()
        if self._pump_id:
            self.panel.root.after_cancel(self._pump_id)
            self._pump_id = None
        if self.window:
            from PySide6.QtCore import QCoreApplication, QEvent
            self.window.hide()
            self.window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.window = None
