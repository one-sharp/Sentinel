"""Desktop preferences and private notes. This module never imports an LLM."""
from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import threading
import uuid

ROOT = Path(__file__).resolve().parent
from sentinel_paths import user_data_dir
DEFAULT_HOTKEYS = {
    "toggle_window": "Ctrl+Alt+Shift+Space",
    "quick_note": "Ctrl+Alt+Shift+N",
    "toggle_listening": "Ctrl+Alt+Shift+L",
    "toggle_fold": "Ctrl+Alt+Shift+M",
}
ACTION_LABELS = {"toggle_window": "显示 / 隐藏", "quick_note": "私人快速笔记",
                 "toggle_listening": "开始 / 停止监听", "toggle_fold": "展开 / 折叠"}
NOTE_KINDS = ("我在想", "我想知道", "我想说")


class DesktopPreferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else user_data_dir() / "desktop_settings.json"
        self.error = ""
        self.value = {"version": 1, "topmost": True, "hotkeys": dict(DEFAULT_HOTKEYS)}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict) or not isinstance(saved.get("hotkeys", {}), dict):
                    raise ValueError("设置格式不正确")
                self.value["topmost"] = bool(saved.get("topmost", True))
                self.value["hotkeys"].update({k:v for k,v in saved.get("hotkeys", {}).items() if k in DEFAULT_HOTKEYS})
            except (OSError, ValueError) as exc:
                self.error = f"桌面设置读取失败，已使用默认值：{exc}"
                logging.exception(self.error)

    def save(self, **fields):
        value = self.value | fields
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.value = value


class NoteStore:
    """Append-only private records, independent of speech and analysis logs.

    on_note_saved is an explicit local extension hook. It has no default LLM or
    network subscriber; connecting future processing requires a deliberate change.
    """
    def __init__(self, directory=None, on_note_saved=None):
        self.directory = Path(directory) if directory else user_data_dir() / "private_notes"
        self.lock = threading.Lock()
        self.on_note_saved = on_note_saved

    def _append(self, filename, record):
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / filename).open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
                file.flush()
                os.fsync(file.fileno())

    def save(self, text, note_kind, session_id, *, asr_log=None, draft=False):
        if note_kind not in NOTE_KINDS:
            raise ValueError("请选择有效的笔记类型")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("笔记内容为空")
        record = {"type": "private_note", "note_id": uuid.uuid4().hex,
                  "note_kind": note_kind, "text": text, "session_id": session_id,
                  "asr_log": asr_log, "draft": bool(draft), "source": "private_keyboard_input",
                  "spoken": False, "send_to_llm": False,
                  "created_at": datetime.now().astimezone().isoformat(timespec="milliseconds")}
        self._append("notes.jsonl", record)
        if self.on_note_saved:
            try:
                self.on_note_saved(dict(record))
            except Exception:
                logging.exception("Local note subscriber failed after note was saved")
        return record

    def bind_session(self, session_id, asr_log):
        self._append("sessions.jsonl", {"type": "session_binding", "session_id": session_id,
                                       "asr_log": str(asr_log), "created_at": datetime.now().astimezone().isoformat()})

    def read(self, session_id=None):
        path = self.directory / "notes.jsonl"
        if not path.exists():
            return []
        records = []
        with self.lock, path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, 1):
                try:
                    row = json.loads(line)
                    if row.get("type") == "private_note" and (session_id is None or row.get("session_id") == session_id):
                        records.append(row)
                except (ValueError, AttributeError):
                    logging.warning("Private note line %d is incomplete; preserving file", line_number)
        return records


def new_session_id():
    return "desktop-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]
