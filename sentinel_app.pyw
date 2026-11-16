"""Double-click desktop entry point using the existing Sentinel environment."""
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
from sentinel_paths import user_data_dir, logs_dir


def main():
    user_data_dir().mkdir(parents=True, exist_ok=True)
    os.chdir(user_data_dir())
    output = None
    guard = None
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        from sentinel_windows import InstanceGuard
        guard = InstanceGuard()
        if not guard.acquire():
            return
        log_dir = logs_dir()
        log_dir.mkdir(exist_ok=True)
        app_log = log_dir / ("app-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".log")
        output = app_log.open("x", encoding="utf-8", buffering=1)
        # pythonw has no stdout/stderr. Existing ASR/LLM print calls still work.
        sys.stdout = sys.stderr = output
        logging.basicConfig(stream=output, level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s", force=True)
        logging.info("Sentinel desktop opened; waiting for Start")
        if "--self-test" in sys.argv:
            from sentinel_package_test import run_self_test
            if not run_self_test(llm="--test-llm" in sys.argv, microphone="--test-microphone" in sys.argv):
                raise SystemExit(1)
            return
        from sentinel_live import MODEL_DIR, create_parser, run
        from sentinel_panel import run_panel

        args = create_parser().parse_args([])
        args.model_dir = MODEL_DIR
        args.llm = True
        args.duration = 0
        args.speakers = True
        run_panel(args, run)
        logging.info("Sentinel desktop closed; sessions cleaned up")
    except Exception:
        detail = traceback.format_exc()
        if output:
            logging.error("Desktop startup failed\n%s", detail)
        import ctypes
        location = str(app_log) if output else str(logs_dir())
        ctypes.windll.user32.MessageBoxW(None, "Sentinel 启动失败：\n" + detail[-1600:]
                                        + "\n日志：" + location, "Sentinel", 0x10)
    finally:
        if output:
            logging.shutdown()
            sys.stdout, sys.stderr = old_stdout, old_stderr
            output.close()
        if guard:
            guard.release()


if __name__ == "__main__":
    main()
