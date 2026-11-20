#!/usr/bin/env python3
"""Sentinel Phase 1: local streaming ASR measurement + optional asynchronous LLM observer.

Microphone ASR uses the sherpa-onnx OnlineRecognizer Paraformer interface.
No audio leaves this computer in ASR-only mode. With --llm, transcript excerpts
are sent to the user-configured LLM API. No audio recordings are saved.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import statistics
import threading
import time
import traceback
import unicodedata
from collections import deque
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sentinel_paths import ASR_MODEL_DIR, logs_dir
from sentinel_audio import prepare_microphone, microphone_stream, MicrophoneUnavailable
MODEL_DIR = ASR_MODEL_DIR
STOP = object()


def norm_zh(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return "".join(ch for ch in s if unicodedata.category(ch)[0] not in ("P", "Z", "C", "S"))


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = norm_zh(reference), norm_zh(hypothesis)
    if not ref:
        raise ValueError("参考文本为空")
    return edit_distance(ref, hyp) / len(ref)


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(p / 100 * len(values)) - 1)]


class Log:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.file = open(path, "x", encoding="utf-8")
        self.lock = threading.Lock()

    def write(self, kind: str, **fields):
        with self.lock:
            if self.file.closed:
                return
            self.file.write(json.dumps({
                "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "type": kind, **fields
            }, ensure_ascii=False) + "\n")
            self.file.flush()

    def close(self):
        with self.lock:
            self.file.close()


from sentinel_observer import LLMObserver, SCENES, parse_json_response, check_llm_connection


def build_recognizer(model_dir: Path, threads: int, silence: float):
    import sherpa_onnx
    names = ["encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"]
    missing = [n for n in names if not (model_dir / n).exists()]
    if missing:
        raise FileNotFoundError(f"缺少模型文件 {missing}；目录：{model_dir.resolve()}。请按 DEVELOPMENT.md 恢复语音模型后重试。")
    return sherpa_onnx.OnlineRecognizer.from_paraformer(
        tokens=str(model_dir / "tokens.txt"),
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        num_threads=threads,
        sample_rate=16000,
        feature_dim=80,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=silence,
        rule3_min_utterance_length=300,
    )


def list_devices():
    import sounddevice as sd
    print(sd.query_devices())


def run(args, on_event=None, stop_event=None):
    emit = on_event or (lambda **kwargs: None)
    stop_event = stop_event or threading.Event()

    output_dir = Path(getattr(args, "log_dir", None) or logs_dir())
    # Clock resolution can be coarser than the filename's microsecond format.
    log_path = output_dir / ("session-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                             + "-" + uuid4().hex + ".jsonl")
    logger = Log(log_path)
    observer = None
    speaker = None
    speaker_events = queue.Queue()
    controls = getattr(args, "controls", queue.Queue())
    enrollment = getattr(args, "enroll_only", False)
    use_speakers = getattr(args, "speakers", False) or enrollment
    ledger = None
    # Set reporting variables before loading optional model/API dependencies so
    # an installation error cannot be masked by the cleanup/reporting block.
    session_start = time.perf_counter()
    previous_partial = ""
    finals = []
    first_partial_delays = []
    endpoint_delays = []
    revisions = 0
    decode_total = 0.0
    audio_total = 0.0
    capture_started = False
    drops = [0]
    status = [0]
    try:
        from sentinel_speakers import SpeakerWorker, TranscriptLedger
        def transcript_event(**event):
            # Invalidate before exposing a correction to the UI. Old in-flight
            # requests must not resurrect claims based on superseded roles.
            if event.get('kind') in ('attribution_update', 'speaker_roles') and observer:
                event['source_epoch'] = observer.invalidate_context()
            emit(**event)

        ledger = TranscriptLedger(logger, transcript_event)
        logger.write("session_start", model=str(args.model_dir), llm=bool(args.llm and not enrollment),
                     sample_rate=args.sample_rate, scene=args.scene, enrollment=enrollment,
                     asr_enabled=not enrollment, audio_timestamp_clock="device_adc_or_capture_frame_counter")
        if enrollment and not getattr(args, "enrollment_consent", False):
            raise ValueError("录入前需要同意在本机保存本人声纹特征")
        emit(kind="session", log_path=str(log_path.resolve()))
        if stop_event.is_set():
            return
        import numpy as np
        import psutil

        emit(kind="startup", text="正在检查麦克风…")
        microphone = prepare_microphone(args.device, args.sample_rate)
        logger.write("audio_input", device=microphone.device, name=microphone.name,
                     sample_rate=microphone.sample_rate)
        if stop_event.is_set():
            return
        emit(kind="startup", text="正在加载本地语音模型…")
        recognizer = None if enrollment else build_recognizer(args.model_dir, args.threads, args.endpoint_silence)
        # Model loading cannot be interrupted; honour Stop before opening devices/API.
        if stop_event.is_set():
            return
        if args.llm and not enrollment:
            observer = LLMObserver(logger, args.scene, args.llm_interval,
                                   on_event=emit, goal=args.goal)
        if stop_event.is_set():
            return
        if use_speakers:
            speaker = SpeakerWorker(logger, lambda **evt: speaker_events.put(evt),
                                    enrollment=enrollment, consent=getattr(args, "save_test_audio", False),
                                    clip_dir=log_path.with_suffix("").with_name(log_path.stem + "-clips"))
            while not speaker.ready.wait(.05):
                if stop_event.is_set():
                    return
            if speaker.failure:
                raise RuntimeError(speaker.failure)
        stream = recognizer.create_stream() if recognizer else None
        process = psutil.Process()
        process.cpu_percent(None)
        sample_rate = microphone.sample_rate
        audio_q = queue.Queue(maxsize=32)
        drops = [0]
        status = [0]
        speech_started = None
        last_voice = None
        last_change = time.perf_counter()
        previous_partial = ""
        finals = []
        session_start = time.perf_counter()
        last_metric = session_start
        utterance_start = None
        utterance_end = None
        utterance_origin = 0.
        audio_end = 0.
        capture_frames = [0]
        adc_origin = [None]

        def context_partial(text):
            return "[unknown 角色=未绑定 临时识别，身份尚未确认] " + text[-200:] if use_speakers and text else text

        def drain_speakers():
            changed = False
            while not speaker_events.empty():
                evt = speaker_events.get_nowait()
                if evt["kind"] in ("speaker_ready", "speaker_segment"):
                    for name, role in evt.get("speakers", {}).items():
                        ledger.roles.setdefault(name, role)
                    evt["speakers"] = dict(ledger.roles)
                if evt["kind"] == "enrollment_complete":
                    stop_event.set()
                if evt["kind"] == "speaker_error" and enrollment:
                    # A short enrollment can continue; model/runtime failure stops.
                    if speaker and speaker.failure:
                        stop_event.set()
                emit(**evt)
            if speaker and not enrollment:
                changed = ledger.refresh(speaker.timeline)
            while not controls.empty():
                command = controls.get_nowait()
                try:
                    kind = command["kind"]
                    if kind == "role" and speaker and command["speaker_id"] in ledger.roles:
                        ledger.role(command["speaker_id"], command["role"])
                        speaker.commands.put(command)
                        transcript_event(kind="speaker_roles", speakers=dict(ledger.roles))
                        changed = True
                    elif kind == "correct" and speaker:
                        name = command["speaker_id"]
                        if name not in ledger.roles:
                            raise ValueError("未知说话人 ID")
                        ledger.correct(command["turn_id"], name, command.get("reason", "manual_correction"))
                        changed = True
                    elif kind == "finish_enrollment" and speaker:
                        speaker.commands.put(command)
                except Exception as exc:
                    logger.write("speaker_control_error", error=str(exc))
                    emit(kind="speaker_error", text=str(exc))
            if changed and observer and not stop_event.is_set():
                observer.submit(ledger.render(), context_partial(previous_partial), force=True)

        def callback(indata, frames, callback_time, callback_status):
            if callback_status:
                status[0] += 1
            samples = indata[:, 0].copy()
            # Keep the ADC/sample clock even when either queue drops a block.
            adc = getattr(callback_time, "inputBufferAdcTime", 0.)
            if adc and adc_origin[0] is None:
                adc_origin[0] = adc - capture_frames[0] / sample_rate
            start_s = adc - adc_origin[0] if adc and adc_origin[0] is not None else capture_frames[0] / sample_rate
            capture_frames[0] += frames
            payload = (samples, time.perf_counter(), start_s)
            if speaker:
                speaker.feed(samples, sample_rate, start_s, bool(callback_status))
            try:
                audio_q.put_nowait(payload)
            except queue.Full:
                drops[0] += 1

        print("[模式] ", "ASR + LLM 事件观察" if observer else "仅本地 ASR 测试")
        print("[模型] ", args.model_dir.resolve())
        print("[日志] ", log_path.resolve())
        print("[控制] Ctrl+C 结束；可使用 --duration 180 自动测试三分钟\n")
        with microphone_stream(microphone, callback=callback) as capture:
            capture_started = True
            if stop_event.is_set():
                return
            emit(kind="listening", llm=bool(observer), model=str(args.model_dir),
                 device=microphone.name, sample_rate=sample_rate)
            while True:
                drain_speakers()
                now = time.perf_counter()
                if (args.duration and now - session_start >= args.duration) or stop_event.is_set():
                    break
                try:
                    samples, captured_at, block_start = audio_q.get(timeout=0.1)
                except queue.Empty:
                    if getattr(capture, "active", True) is False:
                        raise MicrophoneUnavailable("麦克风已断开或停止输入。请检查连接后再次开始监听。")
                    continue
                audio_total += len(samples) / sample_rate
                audio_end = block_start + len(samples) / sample_rate
                if enrollment:
                    continue
                rms = float(np.sqrt(np.mean(samples * samples)))
                if rms >= args.energy_threshold:
                    last_voice = captured_at
                    if speech_started is None:
                        speech_started = captured_at
                    if utterance_start is None:
                        utterance_start = block_start
                    utterance_end = audio_end

                stream.accept_waveform(sample_rate, samples)
                while not stop_event.is_set() and recognizer.is_ready(stream):
                    t0 = time.perf_counter()
                    recognizer.decode_stream(stream)
                    decode_total += time.perf_counter() - t0

                if stop_event.is_set():
                    break

                result = recognizer.get_result(stream).strip()
                if result != previous_partial:
                    if previous_partial and result and not result.startswith(previous_partial):
                        revisions += 1
                    if result and not previous_partial and speech_started is not None:
                        delay = time.perf_counter() - speech_started
                        first_partial_delays.append(delay)
                        print("[首字近似延迟] %.2fs" % delay)
                    if result:
                        print("[临时] " + result[-130:], flush=True)
                    logger.write("partial", text=result, elapsed_s=round(time.perf_counter() - session_start, 3),
                                 start_s=utterance_start if utterance_start is not None else block_start,
                                 end_s=audio_end, speaker_id="unknown", role="未绑定", confidence=0.,
                                 reason="partial_unaligned")
                    previous_partial = result
                    last_change = time.perf_counter()
                    if result:
                        emit(kind="partial", text=context_partial(result[-260:]))

                if observer and result:
                    # Long uninterrupted speech must still be analyzed periodically.
                    # The prompt labels this text as tentative, not confirmed.
                    now_for_llm = time.perf_counter()
                    if (now_for_llm - last_change >= args.stable_seconds
                            or now_for_llm - observer.last_submit_at >= observer.min_seconds):
                        observer.submit(ledger.render() if use_speakers else "\n".join(finals[-16:]),
                                        context_partial(result), force=False)

                if recognizer.is_endpoint(stream):
                    text = recognizer.get_result(stream).strip()
                    if text:
                        finals.append(text)
                        stamp = time.perf_counter() - session_start
                        print("\n[确认] %s   (t=%.1fs)\n" % (text, stamp), flush=True)
                        ledger.add(text, utterance_origin, audio_end,
                                   speaker.timeline if speaker else None)
                        if observer:
                            observer.submit(ledger.render() if use_speakers else "\n".join(finals[-16:]), "", force=True)
                        if last_voice is not None:
                            endpoint_delays.append(time.perf_counter() - last_voice)
                    recognizer.reset(stream)
                    previous_partial = ""
                    speech_started = None
                    last_voice = None
                    utterance_start = utterance_end = None
                    utterance_origin = audio_end
                    last_change = time.perf_counter()

                if time.perf_counter() - last_metric > 15:
                    now = time.perf_counter()
                    cpu_percent = process.cpu_percent(None)
                    print("\n[运行] %.0fs | 已处理音频 %.0fs | 解码RTF≈%.3f | CPU≈%.0f%% | 队列 %d | 丢块 %d | 临时改字 %d" %
                          (now - session_start, audio_total, decode_total / max(audio_total, .1),
                           cpu_percent, audio_q.qsize(), drops[0], revisions), flush=True)
                    last_metric = now
                    logger.write("performance", cpu_percent=cpu_percent,
                                 asr_queue=audio_q.qsize(), asr_dropped_blocks=drops[0],
                                 audio_clock_end_s=audio_end, **(speaker.stats() if speaker else {}))
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在结束测试…")
    except Exception as exc:
        logger.write("error", error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        stop_event.set()
        try:
            if speaker:
                speaker.stop()
                if 'drain_speakers' in locals():
                    drain_speakers()
            if observer:
                emit(kind="stopping_analysis")
                # InputStream has already closed. Wait here, in the ASR worker,
                # so no LLM callbacks or log writes can outlive this session.
                observer.stop()
            # Flush the last partial as a partial, not a false 'final'.
            if previous_partial:
                logger.write("unfinished_partial", text=previous_partial, speaker_id="unknown", role="未绑定",
                             start_s=locals().get("utterance_start"), end_s=locals().get("audio_end"), confidence=0.)
            final_text = "".join(finals)
            output_dir.mkdir(parents=True, exist_ok=True)
            if not enrollment and capture_started:
                (output_dir / "last-transcript.txt").write_text("\n".join(finals), encoding="utf-8")
            summary = {
                "duration_s": round(time.perf_counter() - session_start, 2),
                "audio_processed_s": round(audio_total, 2),
                "decode_rtf_approx": round(decode_total / max(audio_total, .1), 4),
                "first_partial_p50_s": percentile(first_partial_delays, 50),
                "first_partial_p95_s": percentile(first_partial_delays, 95),
                "endpoint_delay_p95_s": percentile(endpoint_delays, 95),
                "partial_revisions": revisions,
                "audio_dropped_blocks": drops[0],
                "audio_callback_status_count": status[0],
                "confirmed_segments": len(finals),
                "llm_requests": observer.requests if observer else 0,
                "llm_failures": observer.failures if observer else 0,
                "llm_completed": observer.completed if observer else 0,
                "llm_alerts": observer.alerts if observer else 0,
                "llm_pending_coalesced": observer.coalesced if observer else 0,
                "llm_last_latency_s": round(observer.last_latency_s, 2) if observer and observer.last_latency_s else None,
                "speaker_manual_reported_errors": ledger.manual_corrections if ledger else 0,
                "speaker_error_rate": None,  # Requires human-labelled ground truth.
                **(speaker.stats() if speaker else {}),
            }
            if args.reference:
                reference = args.reference.read_text(encoding="utf-8")
                summary["cer"] = round(cer(reference, final_text), 4)
                summary["reference_chars"] = len(norm_zh(reference))
            logger.write("summary", **summary)
            print("\n===== Sentinel 测试总结 =====")
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            print("日志: " + str(log_path.resolve()))
            print("转写: " + str((output_dir / "last-transcript.txt").resolve()))
        finally:
            logger.close()
        emit(kind="stopped", summary=summary, log_path=str(log_path.resolve()))


def create_parser():
    parser = argparse.ArgumentParser(description="Sentinel 实时ASR速度/稳定性测试 + LLM持续观察")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--check-audio", action="store_true", help="检查麦克风与采样率，不录音、不加载模型或请求 API")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--log-dir", type=Path, default=None, help="日志目录；默认当前用户 LocalAppData/Sentinel/logs")
    parser.add_argument("--device", type=int, default=None, help="输入设备索引")
    parser.add_argument("--sample-rate", type=int, default=48000, help="麦克风采样率，默认48000")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--duration", type=float, default=0, help="运行秒数，0表示手动结束")
    parser.add_argument("--endpoint-silence", type=float, default=1.2)
    parser.add_argument("--energy-threshold", type=float, default=0.015, help="仅用于近似首字延迟测量")
    parser.add_argument("--reference", type=Path, default=None, help="UTF-8人工参考文本，用于计算字错误率CER")
    parser.add_argument("--llm", action="store_true", help="启用LLM自动观察，需配置.env")
    parser.add_argument("--scene", choices=list(SCENES), default="negotiation")
    parser.add_argument("--llm-interval", type=float, default=3.0, help="LLM最短分析间隔，默认3秒")
    parser.add_argument("--stable-seconds", type=float, default=0.6, help="临时文本短暂停顿后可分析")
    parser.add_argument("--goal", default="", help="本场关注目标，如确认合同付款期限和交付责任")
    parser.add_argument("--check-llm", action="store_true", help="独立检查LLM Key和接口是否可用")
    parser.add_argument("--panel", action="store_true", help="打开实时大字报桌面窗口（Tkinter）")
    parser.add_argument("--speakers", action="store_true", help="启用本地 CAM++ 说话人区分，需先安装声纹模型")
    parser.add_argument("--save-test-audio", action="store_true", help="已获参与者同意，保存最多30秒必要测试片段")
    return parser


def main():
    args = create_parser().parse_args()
    if args.list_devices:
        list_devices()
        return
    if args.check_audio:
        try:
            microphone = prepare_microphone(args.device, args.sample_rate)
        except (MicrophoneUnavailable, ValueError) as exc:
            print(str(exc))
            raise SystemExit(1)
        print(f"麦克风可用：{microphone.name} · {microphone.sample_rate} Hz · 单声道")
        return
    if args.check_llm:
        check_llm_connection()
        return
    if args.panel:
        from sentinel_panel import run_panel
        run_panel(args, run)
    else:
        run(args)


if __name__ == "__main__":
    main()
