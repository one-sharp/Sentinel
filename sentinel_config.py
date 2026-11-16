"""Per-user API configuration; frozen builds never load the development .env."""
import base64
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path

from sentinel_paths import FROZEN, SOURCE_ROOT, user_data_dir

ENV_KEYS = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_NETWORK_MODE", "EVENT_GOAL")
DEFAULTS = {"LLM_API_KEY":"", "LLM_BASE_URL":"https://api.deepseek.com", "LLM_MODEL":"deepseek-flash",
            "LLM_NETWORK_MODE":"direct", "EVENT_GOAL":"关注重要变化和需要当场确认的内容"}


def config_path():
    return user_data_dir() / "api_config.json"


def _dpapi(data, decrypt=False):
    if os.name != "nt":
        raise OSError("API Key 本地加密需要 Windows DPAPI")
    class Blob(C.Structure):
        _fields_ = [("size", W.DWORD), ("data", C.POINTER(C.c_ubyte))]
    crypt = C.WinDLL("crypt32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    raw = C.create_string_buffer(data)
    source = Blob(len(data), C.cast(raw, C.POINTER(C.c_ubyte)))
    result = Blob()
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [C.POINTER(Blob),W.LPVOID,C.POINTER(Blob),W.LPVOID,W.LPVOID,W.DWORD,C.POINTER(Blob)]
        args = (C.byref(source),None,None,None,None,1,C.byref(result))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [C.POINTER(Blob),W.LPCWSTR,C.POINTER(Blob),W.LPVOID,W.LPVOID,W.DWORD,C.POINTER(Blob)]
        args = (C.byref(source),"Sentinel API",None,None,None,1,C.byref(result))
    fn.restype = W.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [W.HLOCAL], W.HLOCAL
    if not fn(*args):
        raise C.WinError(C.get_last_error())
    try:
        return C.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(C.cast(result.data, W.HLOCAL))


def load_saved_config():
    path = config_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    values = {k:v for k,v in data.items() if k in ENV_KEYS and k != "LLM_API_KEY"}
    if data.get("api_key_dpapi"):
        values["LLM_API_KEY"] = _dpapi(base64.b64decode(data["api_key_dpapi"]), decrypt=True).decode("utf-8")
    return values


def load_api_environment():
    if not FROZEN:
        from dotenv import load_dotenv
        load_dotenv(SOURCE_ROOT / ".env")
    # An explicit GUI-saved profile takes priority. No developer .env is edited.
    saved = load_saved_config()
    for key, value in saved.items():
        os.environ[key] = str(value)


def api_settings():
    load_api_environment()
    return {key: os.getenv(key, DEFAULTS[key]).strip() for key in ENV_KEYS}


def save_api_settings(values):
    from urllib.parse import urlsplit
    key = values.get("LLM_API_KEY", "").strip()
    model = values.get("LLM_MODEL", "").strip()
    url = values.get("LLM_BASE_URL", "").strip().rstrip("/")
    mode = values.get("LLM_NETWORK_MODE", "direct")
    parsed = urlsplit(url)
    if not key or not model or "\n" in key or "\r" in key:
        raise ValueError("请填写有效的 API Key 和模型名称")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("API 地址需要使用有效的 HTTPS 地址")
    if mode not in ("direct", "system"):
        raise ValueError("连接方式只能是 direct 或 system")
    data = {"version":1, "LLM_BASE_URL":url, "LLM_MODEL":model, "LLM_NETWORK_MODE":mode,
            "EVENT_GOAL":values.get("EVENT_GOAL", DEFAULTS["EVENT_GOAL"]),
            "api_key_dpapi":base64.b64encode(_dpapi(key.encode("utf-8"))).decode("ascii")}
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def needs_api_setup():
    try:
        settings = api_settings()
        return not settings["LLM_API_KEY"] or "YOUR_" in settings["LLM_API_KEY"]
    except (OSError, ValueError):
        return True
