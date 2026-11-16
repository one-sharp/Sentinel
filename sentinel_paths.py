"""Read-only application resources vs per-user writable data (source or frozen)."""
import os
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
RESOURCE_ROOT = Path(sys.executable).resolve().parent / "resources" if FROZEN else SOURCE_ROOT
ASSETS_DIR = BUNDLE_ROOT / "assets"
ASR_MODEL_DIR = RESOURCE_ROOT / "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
SPEAKER_MODEL_DIR = RESOURCE_ROOT / "speaker_models"


def user_data_dir():
    # Explicit override is for isolated QA / portable developer testing only.
    override = os.getenv("SENTINEL_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = os.getenv("LOCALAPPDATA")
    return Path(base) / "Sentinel" if base else Path.home() / "AppData/Local/Sentinel"


def logs_dir():
    return user_data_dir() / "logs"


def profile_path():
    return user_data_dir() / "voice_profiles/self.json"
