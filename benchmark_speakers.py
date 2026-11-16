"""Replay a consented/local or public PCM16 WAV through the real live pipeline.

No physical microphone, no LLM requests, no copy of input audio is saved.
Results are throughput observations, NOT a speaker-accuracy test.
"""
import argparse
import contextlib
from datetime import datetime
import json
import math
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import wave

import numpy as np
from scipy.signal import resample_poly

from sentinel_live import MODEL_DIR, create_parser, run
from sentinel_speakers import ROOT, SpeakerConfig, SpeakerWorker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=0, help="0 runs the whole input, otherwise repeat/trim to this length")
    args = parser.parse_args()
    source = args.audio.resolve()
    with wave.open(str(source)) as file:
        if file.getsampwidth() != 2 or file.getnchannels() != 1:
            raise ValueError("需要单声道 PCM16 WAV")
        rate = file.getframerate()
        audio = np.frombuffer(file.readframes(file.getnframes()), "<i2").astype(np.float32) / 32768
    if args.seconds:
        audio = np.tile(audio, math.ceil(args.seconds * rate / len(audio)))[:int(args.seconds * rate)]
    divisor = math.gcd(rate, 48000)
    audio = resample_poly(audio, 48000//divisor, rate//divisor).astype(np.float32)
    audio = np.concatenate((audio, np.zeros(96000, np.float32)))
    directory = ROOT / "logs" / ("benchmark-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    directory.mkdir(parents=True)
    previous = Path.cwd()
    results = {}
    try:
        for enabled in (False, True):
            mode = "speakers" if enabled else "baseline"
            working = directory / mode
            working.mkdir()
            os.chdir(working)
            stop = threading.Event()

            class Replay:
                def __init__(self, **kwargs):
                    self.callback = kwargs["callback"]
                    self.quit = threading.Event()
                def __enter__(self):
                    self.thread = threading.Thread(target=self.feed)
                    self.thread.start()
                    return self
                def feed(self):
                    deadline = time.perf_counter()
                    for offset in range(0, len(audio), 4800):
                        if self.quit.is_set() or stop.is_set():
                            break
                        block = audio[offset:offset+4800, None]
                        self.callback(block, len(block), SimpleNamespace(inputBufferAdcTime=100+offset/48000), False)
                        deadline += len(block)/48000
                        self.quit.wait(max(0, deadline-time.perf_counter()))
                    stop.set()
                def __exit__(self, *args):
                    self.quit.set()
                    self.thread.join()

            def isolated_worker(*worker_args, **kwargs):
                kwargs["config"] = SpeakerConfig(profile=directory / "no-self-profile.json")
                return SpeakerWorker(*worker_args, **kwargs)

            options = create_parser().parse_args(["--speakers"] if enabled else [])
            options.model_dir = ROOT / MODEL_DIR
            options.log_dir = working / "logs"
            started = time.perf_counter()
            with (working / "console.log").open("w", encoding="utf-8") as output, \
                    contextlib.redirect_stdout(output), patch("sounddevice.InputStream", Replay), \
                    patch("sentinel_speakers.SpeakerWorker", side_effect=isolated_worker):
                run(options, stop_event=stop)
            rows = [json.loads(line) for line in next((working / "logs").glob("session-*.jsonl")).read_text(encoding="utf-8").splitlines()]
            results[mode] = {"summary": rows[-1], "wall_s": round(time.perf_counter()-started, 2),
                             "performance": [r for r in rows if r["type"] == "performance"],
                             "final_texts": [r["text"] for r in rows if r["type"] == "final"]}
            print(mode, json.dumps(results[mode]["summary"], ensure_ascii=False), flush=True)
        results["source"] = str(source)
        results["input_audio_s"] = len(audio)/48000
        results["identical_confirmed_texts"] = results["baseline"]["final_texts"] == results["speakers"]["final_texts"]
        (directory / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("RESULT:", directory / "results.json", flush=True)
    finally:
        os.chdir(previous)


if __name__ == "__main__":
    main()
