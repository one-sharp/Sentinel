"""Local, bounded speaker branch. No microphone is opened in this module.

CAM++ embeddings are similarity evidence, not calibrated identity probabilities.
VAD + embedding consistency is NOT a general overlapping-speech separator.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import queue
import threading
import time
import traceback
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
from sentinel_paths import SPEAKER_MODEL_DIR, profile_path
MODEL = SPEAKER_MODEL_DIR / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
VAD_MODEL = SPEAKER_MODEL_DIR / "silero_vad.onnx"
PROFILE = profile_path()
ROLES = ("未绑定", "我方", "对方", "第三方")
RATE = 16000


def unit(vector):
    value = np.asarray(vector, dtype=np.float32)
    if value.ndim != 1 or not np.isfinite(value).all() or np.linalg.norm(value) < 1e-8:
        raise ValueError("无效的声纹特征")
    return value / np.linalg.norm(value)


@dataclass
class SpeakerConfig:
    model: Path = MODEL
    vad_model: Path = VAD_MODEL
    profile: Path = PROFILE
    threshold: float = .65
    margin: float = .12
    novelty: float = .40
    consistency: float = .50
    min_seconds: float = 1.2
    new_seconds: float = 2.4
    enrollment_seconds: float = 15.0


def save_profile(path, embedding, model_hash, seconds):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"version": 1, "model_sha256": model_hash, "embedding": unit(embedding).tolist(),
             "speech_seconds": round(seconds, 3), "created_at": datetime.now().astimezone().isoformat()}
    temp = path.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(value), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_profile(path, model_hash, dim):
    path = Path(path)
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("model_sha256") != model_hash or len(value.get("embedding", [])) != dim:
        raise ValueError("本地声纹与当前 CAM++ 模型不匹配，请删除后重新录入")
    return unit(value["embedding"])


class SpeakerBank:
    """Worker-owned sherpa manager with conservative novelty/margin gates."""

    def __init__(self, manager, config, own=None):
        self.manager, self.config = manager, config
        self.vectors = {}
        self.roles = {"unknown": "未绑定"}
        self.next_id = 1  # A is reserved for the explicitly enrolled user.
        if own is not None:
            self.add("Speaker A", own, "我方")

    def add(self, name, vector, role="未绑定"):
        vector = unit(vector)
        if not self.manager.add(name, vector):
            raise RuntimeError("注册说话人失败：" + name)
        self.vectors[name] = vector
        self.roles[name] = role

    def classify(self, vector, seconds, consistent=True):
        if seconds < self.config.min_seconds:
            return "unknown", 0., "short_speech"
        if not consistent:
            return "unknown", 0., "mixed_or_overlap"
        vector = unit(vector)
        scores = sorted(((name, float(self.manager.score(name, vector))) for name in self.vectors),
                        key=lambda x: x[1], reverse=True)
        best = scores[0][1] if scores else 0.
        matched = self.manager.search(vector, self.config.threshold)
        margin = best - scores[1][1] if len(scores) > 1 else 1.
        if matched and margin >= self.config.margin:
            return matched, best, "matched"
        # A near miss is ambiguous, not evidence of a new person.
        if scores and (best >= self.config.novelty or matched):
            return "unknown", best, "low_margin_or_similarity"
        if seconds < self.config.new_seconds or len(self.vectors) >= 6:
            return "unknown", best, "insufficient_new_speaker_evidence"
        name = "Speaker " + chr(ord("A") + self.next_id)
        self.next_id += 1
        self.add(name, vector)
        return name, 1., "new_cluster"  # Prototype self-similarity, NOT 100% accuracy.


class SpeakerTimeline:
    """Acoustic spans on the capture clock; ASR text order is never consulted."""

    def __init__(self):
        self.spans = deque(maxlen=2000)
        self.settled_until = 0.
        self.lock = threading.Lock()

    def add(self, span):
        with self.lock:
            self.spans.append(dict(span))

    def settle(self, timestamp):
        with self.lock:
            self.settled_until = max(self.settled_until, timestamp)

    def align(self, start, end):
        with self.lock:
            spans = [s for s in self.spans if s["end_s"] > start and s["start_s"] < end]
            settled = self.settled_until
        unknown = {"speaker_id": "unknown", "role": "未绑定", "confidence": 0., "reason": "pending_audio"}
        if end > settled + .02:
            return unknown
        if not spans:
            return {**unknown, "reason": "no_speech_evidence"}
        ids = {s["speaker_id"] for s in spans}
        if len(ids) != 1 or "unknown" in ids:
            return {**unknown, "reason": "multiple_or_uncertain_speakers"}
        # Also reject conflicting overlapping acoustic intervals.
        covered = sum(max(0., min(end, s["end_s"]) - max(start, s["start_s"])) for s in spans)
        if covered < .5:
            return {**unknown, "reason": "insufficient_time_coverage"}
        return {"speaker_id": spans[0]["speaker_id"], "role": spans[0]["role"],
                "confidence": min(s["confidence"] for s in spans), "reason": "segment_time_alignment"}


class TranscriptLedger:
    """Bounded working context; original text and every update are in JSONL."""

    def __init__(self, log, emit):
        self.log, self.emit = log, emit
        self.turns = deque(maxlen=128)
        self.serial = 0
        self.roles = {"unknown": "未绑定"}
        self.manual_corrections = 0

    def add(self, text, start, end, timeline=None):
        self.serial += 1
        turn = dict(turn_id=self.serial, text=text, start_s=round(start, 3), end_s=round(end, 3),
                    speaker_id="unknown", role="未绑定", confidence=0., reason="pending_audio", revision=0)
        if timeline:
            turn.update(timeline.align(start, end))
        turn["role"] = self.roles.get(turn["speaker_id"], turn["role"])
        self.turns.append(turn)
        self.log.write("final", **turn, elapsed_s=end)
        self.emit(kind="final", **turn, elapsed=end)
        return turn

    def refresh(self, timeline):
        changed = False
        for turn in self.turns:
            if turn.get("manual") or turn.get("settled"):
                continue
            attribution = timeline.align(turn["start_s"], turn["end_s"])
            attribution["role"] = self.roles.get(attribution["speaker_id"], attribution["role"])
            if attribution["reason"] != "pending_audio":
                turn["settled"] = True
            if any(turn[k] != v for k, v in attribution.items()):
                self.update(turn, attribution, "acoustic_alignment")
                changed = True
        return changed

    def update(self, turn, fields, source):
        before = {k: turn[k] for k in ("speaker_id", "role", "confidence", "reason")}
        turn.update(fields)
        turn["revision"] += 1
        self.log.write("attribution_update", **turn, previous=before, source=source)
        self.emit(kind="attribution_update", **turn)

    def role(self, speaker_id, role):
        if role not in ROLES or speaker_id == "unknown":
            raise ValueError("请选择已知说话人和有效角色")
        previous = self.roles.get(speaker_id, "未绑定")
        self.roles[speaker_id] = role
        self.log.write("speaker_role_update", speaker_id=speaker_id, previous_role=previous, role=role)
        for turn in self.turns:
            if turn["speaker_id"] == speaker_id:
                self.update(turn, {"role": role}, "manual_role")

    def correct(self, turn_id, speaker_id, reason="manual_correction"):
        turn = next((t for t in self.turns if t["turn_id"] == turn_id), None)
        if turn is None:
            raise ValueError("该片段已离开可纠正窗口，请在 JSONL 中复核")
        previous = turn["speaker_id"]
        self.manual_corrections += int(previous != speaker_id)
        self.log.write("speaker_correction", turn_id=turn_id, start_s=turn["start_s"], end_s=turn["end_s"],
                       predicted_id=previous, corrected_id=speaker_id, reported_error=previous != speaker_id)
        self.update(turn, dict(speaker_id=speaker_id, role=self.roles.get(speaker_id, "未绑定"),
                               confidence=None, reason=reason, manual=True), "manual_correction")

    def render(self):
        lines, size = [], 0
        for turn in reversed(self.turns):
            line = (f"[片段#{turn['turn_id']} v{turn['revision']} {turn['start_s']:.2f}-{turn['end_s']:.2f}s "
                    f"{turn['speaker_id']} 角色={turn['role']} 相似度={turn['confidence']} 归属={turn['reason']}] " + turn["text"][:350])
            if size + len(line) > 1850:
                break
            lines.append(line)
            size += len(line) + 1
        return "\n".join(reversed(lines))


class SpeakerWorker:
    """VAD, resampling, embeddings and optional bounded clips on one CPU worker."""

    def __init__(self, log, emit, config=None, enrollment=False, consent=False, clip_dir=None):
        self.log, self.emit = log, emit
        self.config = config or SpeakerConfig()
        self.enrollment, self.consent = enrollment, consent
        self.clip_dir = Path(clip_dir) if clip_dir else None
        self.audio = queue.Queue(maxsize=64)
        self.commands = queue.Queue()
        self.timeline = SpeakerTimeline()
        self.stop_flag, self.ready = threading.Event(), threading.Event()
        self.failure = None
        self.dropped = self.highwater = self.processed = 0
        self.latencies = []
        self.cpu_seconds = 0.
        self.enrolled = []
        self.enrolled_seconds = self.saved_seconds = 0.
        self.finished_enrollment = False
        self.thread = threading.Thread(target=self._run, name="sentinel-speakers", daemon=False)
        self.thread.start()

    def feed(self, samples, sample_rate, start_s, discontinuity=False):
        try:
            self.audio.put_nowait((samples, sample_rate, start_s, discontinuity))
            self.highwater = max(self.highwater, self.audio.qsize())
        except queue.Full:
            self.dropped += 1

    def stop(self):
        self.stop_flag.set()
        self.thread.join()

    def stats(self):
        return {"speaker_segments": self.processed, "speaker_dropped_blocks": self.dropped,
                "speaker_queue_highwater": self.highwater, "speaker_cpu_s": round(self.cpu_seconds, 3),
                "speaker_latency_p95_s": float(np.percentile(self.latencies, 95)) if self.latencies else None,
                "speaker_saved_audio_s": round(self.saved_seconds, 3)}

    def _load(self):
        import sherpa_onnx as sherpa
        for path in (self.config.model, self.config.vad_model):
            if not Path(path).is_file():
                raise FileNotFoundError(f"缺少声纹/VAD 模型：{path}；请双击 setup_speakers.cmd")
        self.model_hash = hashlib.sha256(Path(self.config.model).read_bytes()).hexdigest()
        config = sherpa.SpeakerEmbeddingExtractorConfig(model=str(self.config.model), num_threads=1, provider="cpu")
        if not config.validate():
            raise ValueError("CAM++ 模型配置无效")
        self.extractor = sherpa.SpeakerEmbeddingExtractor(config)
        own = None if self.enrollment else load_profile(self.config.profile, self.model_hash, self.extractor.dim)
        self.bank = SpeakerBank(sherpa.SpeakerEmbeddingManager(self.extractor.dim), self.config, own)
        self.vad_config = sherpa.VadModelConfig()
        self.vad_config.sample_rate = RATE
        self.vad_config.num_threads = 1
        self.vad_config.silero_vad.model = str(self.config.vad_model)
        self.vad_config.silero_vad.min_silence_duration = .35
        self.vad_config.silero_vad.min_speech_duration = .25
        self.vad_config.silero_vad.max_speech_duration = 4.
        self.vad = sherpa.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=15)
        self.buffer = np.empty(0, np.float32)
        self.origin = 0.
        self.expected = None
        self.last_end = 0.
        self.chunked_until = 0
        self.log.write("speaker_config", model=str(self.config.model), model_sha256=self.model_hash,
                       thresholds=vars(self.config) | {"model": str(self.config.model), "vad_model": str(self.config.vad_model),
                                                       "profile": str(self.config.profile)}, enrollment=self.enrollment,
                       save_test_audio_consent=self.consent, overlap_detection="embedding_consistency_only")
        self.emit(kind="speaker_ready", speakers=self.bank.roles, enrollment=self.enrollment)

    def _embed(self, samples):
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=RATE, waveform=np.ascontiguousarray(samples, dtype=np.float32))
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            raise ValueError("声纹有效语音不足")
        return unit(self.extractor.compute(stream))

    def _segment(self, samples, start):
        started = time.perf_counter()
        duration = len(samples) / RATE
        vector, consistent = None, False
        if duration >= self.config.min_seconds:
            vector = self._embed(samples)
            # Reject change/overlap evidence within a VAD chunk. Stable mixtures
            # can still evade this heuristic; they require manual unknown marking.
            if duration >= self.config.new_seconds:
                width = int(self.config.min_seconds * RATE)
                middle = (len(samples) - width) // 2
                vectors = [self._embed(samples[:width]), self._embed(samples[middle:middle+width]),
                           self._embed(samples[-width:])]
                consistent = min(float(a @ b) for a in vectors for b in vectors) >= self.config.consistency
            else:
                consistent = True
        if self.enrollment:
            if vector is not None and consistent and (not self.enrolled or float(vector @ unit(np.mean(self.enrolled, axis=0))) >= self.config.threshold):
                self.enrolled.append(vector)
                self.enrolled_seconds += duration
            self.emit(kind="enrollment_progress", seconds=round(self.enrolled_seconds, 1), target=self.config.enrollment_seconds)
            if self.enrolled_seconds >= self.config.enrollment_seconds:
                self._save_enrollment()
            return
        name, score, reason = self.bank.classify(vector, duration, consistent) if vector is not None else ("unknown", 0., "short_speech")
        span = dict(speaker_id=name, role=self.bank.roles.get(name, "未绑定"), start_s=round(start, 4),
                    end_s=round(start + duration, 4), confidence=round(score, 4), reason=reason,
                    confidence_kind="cosine_similarity_not_probability")
        self.timeline.add(span)
        elapsed = time.perf_counter() - started
        self.latencies.append(elapsed)
        self.latencies = self.latencies[-2000:]
        self.processed += 1
        self.log.write("speaker_segment", **span, inference_s=round(elapsed, 4), queue_depth=self.audio.qsize())
        self.emit(kind="speaker_segment", **span, speakers=dict(self.bank.roles))
        if self.consent and self.clip_dir and self.saved_seconds + duration <= 30. and duration <= 4.5:
            self.clip_dir.mkdir(parents=True, exist_ok=True)
            path = self.clip_dir / f"segment-{self.processed:04d}.wav"
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(RATE)
                out.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            self.saved_seconds += duration
            self.log.write("test_audio_saved", path=str(path), consent=True, **span)

    def _save_enrollment(self):
        if self.finished_enrollment:
            return
        if self.enrolled_seconds < 10:
            self.emit(kind="speaker_error", text="至少需要 10 秒有效语音，请继续单独讲话。")
            return
        save_profile(self.config.profile, np.mean(self.enrolled, axis=0), self.model_hash, self.enrolled_seconds)
        self.finished_enrollment = True
        self.log.write("enrollment_saved", profile=str(self.config.profile), speech_seconds=self.enrolled_seconds,
                       raw_audio_saved=False)
        self.emit(kind="enrollment_complete", seconds=round(self.enrolled_seconds, 1))

    def _drain_vad(self):
        while not self.vad.empty():
            segment = self.vad.front
            self._consume_segment(segment, final=True)
            self.vad.pop()
        # Some Silero releases treat max_speech_duration as a soft limit.
        # Bound our own cache/inference latency instead of waiting for silence.
        if self.vad.is_speech_detected():
            self._consume_segment(self.vad.current_segment, final=False)

    def _consume_segment(self, segment, final):
        samples = np.asarray(segment.samples, dtype=np.float32)
        start = int(segment.start)
        offset = max(0, self.chunked_until - start)
        maximum = int(3.2 * RATE)
        while len(samples) - offset >= maximum or (final and offset < len(samples)):
            length = min(maximum, len(samples) - offset)
            self._segment(samples[offset:offset+length].copy(), self.origin + (start + offset) / RATE)
            offset += length
            self.chunked_until = start + offset
            self.timeline.settle(self.origin + self.chunked_until / RATE)
            if self.finished_enrollment:
                break

    def _run(self):
        cpu = time.thread_time()
        try:
            self._load()
            self.ready.set()
            while not self.stop_flag.is_set() or not self.audio.empty():
                while not self.commands.empty():
                    command = self.commands.get_nowait()
                    if command["kind"] == "role":
                        self.bank.roles[command["speaker_id"]] = command["role"]
                    elif command["kind"] == "finish_enrollment" and self.enrollment:
                        self.vad.flush()
                        self._drain_vad()
                        self._save_enrollment()
                try:
                    samples, rate, start, gap = self.audio.get(timeout=.05)
                except queue.Empty:
                    continue
                end = start + len(samples) / rate
                if self.expected is not None and (gap or abs(start - self.expected) > .025):
                    # Throw away a clipped utterance; never stitch missing audio.
                    self.timeline.add(dict(start_s=self.origin, end_s=start, speaker_id="unknown", role="未绑定",
                                           confidence=0., reason="audio_gap"))
                    self.vad.reset()
                    self.buffer = np.empty(0, np.float32)
                    self.origin = start
                    self.chunked_until = 0
                    self.log.write("speaker_audio_gap", start_s=self.expected, end_s=start)
                elif self.expected is None:
                    self.origin = start
                self.expected = end
                # Streaming block resampling with anti-aliasing, in this worker.
                if rate != RATE:
                    from scipy.signal import resample_poly
                    import math
                    divisor = math.gcd(rate, RATE)
                    samples = resample_poly(samples, RATE // divisor, rate // divisor).astype(np.float32)
                self.buffer = np.concatenate((self.buffer, samples))
                window = self.vad_config.silero_vad.window_size
                while len(self.buffer) >= window:
                    self.vad.accept_waveform(self.buffer[:window])
                    self.buffer = self.buffer[window:]
                    self._drain_vad()
                self.last_end = end
                self.cpu_seconds = time.thread_time() - cpu
                if not self.vad.is_speech_detected():
                    self.timeline.settle(max(self.origin, end - .55))
                if self.finished_enrollment:
                    break
            if not self.enrollment:
                self.vad.flush()
                self._drain_vad()
                self.timeline.settle(self.last_end)
        except Exception as exc:
            self.failure = str(exc)
            self.log.write("speaker_error", error=str(exc), traceback=traceback.format_exc())
            self.emit(kind="speaker_error", text=str(exc))
        finally:
            self.cpu_seconds = time.thread_time() - cpu
            self.ready.set()
            self.enrolled.clear()  # no raw enrollment audio is persisted
            self.log.write("speaker_summary", **self.stats())
