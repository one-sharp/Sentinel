"""Deterministic speaker safety/lifecycle tests; no user recording or API call."""
import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import sherpa_onnx

from sentinel_speakers import (ROOT, SpeakerBank, SpeakerConfig, SpeakerTimeline, SpeakerWorker,
                               TranscriptLedger, load_profile, save_profile, unit)
from test_observer import MemoryLog


def span(start, end, name="Speaker B", role="未绑定", score=.8):
    return dict(start_s=start, end_s=end, speaker_id=name, role=role, confidence=score, reason="matched")


class BankTests(unittest.TestCase):
    def bank(self, own=None):
        return SpeakerBank(sherpa_onnx.SpeakerEmbeddingManager(3), SpeakerConfig(), own)

    def test_anonymous_is_stable_and_does_not_claim_user_identity(self):
        bank = self.bank()
        first = bank.classify([1, 0, 0], 3)
        again = bank.classify([.99, .01, 0], 3)
        second = bank.classify([0, 1, 0], 3)
        self.assertEqual((first[0], again[0], second[0]), ("Speaker B", "Speaker B", "Speaker C"))
        self.assertEqual(bank.roles["Speaker B"], "未绑定")

    def test_enrolled_user_is_A(self):
        bank = self.bank([1, 0, 0])
        self.assertEqual(bank.classify([.98, .02, 0], 3)[0], "Speaker A")
        self.assertEqual(bank.roles["Speaker A"], "我方")

    def test_short_mixed_and_near_match_are_unknown(self):
        bank = self.bank([1, 0, 0])
        self.assertEqual(bank.classify([1, 0, 0], .4)[0], "unknown")
        self.assertEqual(bank.classify([1, 0, 0], 3, consistent=False)[0], "unknown")
        self.assertEqual(bank.classify(unit([.55, .835, 0]), 3)[0], "unknown")
        self.assertEqual(len(bank.vectors), 1)

    def test_close_candidates_are_unknown(self):
        bank = self.bank([1, 0, 0])
        bank.add("Speaker B", unit([.8, .6, 0]))
        self.assertEqual(bank.classify(unit([.9, .3, 0]), 3)[0], "unknown")


class AlignmentTests(unittest.TestCase):
    def test_pending_then_timestamp_alignment(self):
        timeline = SpeakerTimeline()
        timeline.add(span(1, 3))
        self.assertEqual(timeline.align(1, 3)["reason"], "pending_audio")
        timeline.settle(5)
        self.assertEqual(timeline.align(1, 3)["speaker_id"], "Speaker B")
        self.assertEqual(timeline.align(3.5, 4.5)["speaker_id"], "unknown")

    def test_multi_speaker_utterance_is_not_split_by_text_order(self):
        timeline = SpeakerTimeline()
        timeline.add(span(0, 2, "Speaker B"))
        timeline.add(span(2, 4, "Speaker C"))
        timeline.settle(5)
        self.assertEqual(timeline.align(0, 4)["speaker_id"], "unknown")
        self.assertEqual(timeline.align(2.1, 3.9)["speaker_id"], "Speaker C")

    def test_unknown_span_and_capture_gap_poison_attribution(self):
        timeline = SpeakerTimeline()
        timeline.add(span(0, 2))
        timeline.add(span(1.5, 2.5, "unknown"))
        timeline.settle(4)
        self.assertEqual(timeline.align(0, 3)["speaker_id"], "unknown")

    def test_late_evidence_role_changes_and_manual_unknown_preserve_raw_text(self):
        log, events = MemoryLog(), []
        ledger = TranscriptLedger(log, lambda **e: events.append(e))
        timeline = SpeakerTimeline()
        raw = "对方提出六十天，但我们没有同意。"
        turn = ledger.add(raw, 0, 3, timeline)
        timeline.add(span(0, 3))
        timeline.settle(4)
        self.assertTrue(ledger.refresh(timeline))
        ledger.role("Speaker B", "对方")
        self.assertIn("Speaker B 角色=对方", ledger.render())
        self.assertEqual(turn["text"], raw)
        ledger.correct(turn["turn_id"], "unknown", "manual_overlap_or_unknown")
        ledger.refresh(timeline)
        self.assertEqual(turn["speaker_id"], "unknown")
        self.assertEqual(ledger.manual_corrections, 1)
        self.assertEqual(turn["text"], raw)
        self.assertTrue(any(k == "attribution_update" and r["previous"]["speaker_id"] == "unknown" for k,r in log.rows))
        self.assertTrue(any(k == "speaker_correction" for k,r in log.rows))

    def test_llm_context_is_bounded_and_labels_are_kept(self):
        ledger = TranscriptLedger(MemoryLog(), lambda **e: None)
        for i in range(200):
            ledger.add("长文本" * 200, i, i+1)
        self.assertLess(len(ledger.render()), 1900)
        self.assertTrue(ledger.render().startswith("[片段#"))
        self.assertIn("unknown 角色=未绑定", ledger.render())
        self.assertEqual(len(ledger.turns), 128)


class ProfileAndWorkerTests(unittest.TestCase):
    def test_soft_vad_limit_is_hard_chunked_without_duplicate_audio(self):
        worker = SpeakerWorker.__new__(SpeakerWorker)
        worker.origin = 10.
        worker.chunked_until = 0
        worker.timeline = SpeakerTimeline()
        worker.finished_enrollment = False
        clips = []
        worker._segment = lambda samples, start: clips.append((len(samples), start))
        worker._consume_segment(SimpleNamespace(start=100, samples=np.zeros(60000, np.float32)), final=False)
        worker._consume_segment(SimpleNamespace(start=100, samples=np.zeros(110000, np.float32)), final=True)
        self.assertEqual(sum(c[0] for c in clips), 110000)
        self.assertLessEqual(max(c[0] for c in clips), 51200)
        self.assertAlmostEqual(clips[0][1], 10+100/16000)

    def test_raw_audio_requires_consent_and_is_capped(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".sentinel-test-") as name:
            self.assertEqual(Path(name).resolve().parent, ROOT)
            worker = SpeakerWorker.__new__(SpeakerWorker)
            worker.config = SpeakerConfig()
            worker.enrollment = False
            worker.consent = False
            worker.clip_dir = Path(name)
            worker.saved_seconds = 0.
            worker.latencies = []
            worker.processed = 0
            worker.audio = queue.Queue()
            worker.timeline = SpeakerTimeline()
            worker.log = MemoryLog()
            worker.emit = lambda **e: None
            worker._embed = lambda samples: unit([1, 0, 0])
            worker.bank = SimpleNamespace(roles={"Speaker B":"未绑定"}, classify=lambda *a: ("Speaker B", .8, "matched"))
            samples = np.zeros(48000, np.float32)
            worker._segment(samples, 0)
            self.assertEqual(list(Path(name).glob("*.wav")), [])
            worker.consent = True
            for index in range(11):
                worker._segment(samples, 3*index)
            self.assertEqual(worker.saved_seconds, 30.)
            self.assertEqual(len(list(Path(name).glob("*.wav"))), 10)

    def test_profile_is_model_bound_and_has_no_raw_audio(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".sentinel-test-") as name:
            self.assertEqual(Path(name).resolve().parent, ROOT)
            path = Path(name) / "self.json"
            save_profile(path, [1, 0, 0], "hash1", 12)
            np.testing.assert_allclose(load_profile(path, "hash1", 3), [1, 0, 0])
            with self.assertRaises(ValueError):
                load_profile(path, "hash2", 3)
            self.assertNotIn("audio", json.loads(path.read_text()))
            self.assertFalse(list(Path(name).glob("*.wav")))
            path.unlink()
            self.assertIsNone(load_profile(path, "hash1", 3))

    def test_incomplete_enrollment_does_not_replace_profile(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".sentinel-test-") as name:
            self.assertEqual(Path(name).resolve().parent, ROOT)
            path = Path(name) / "self.json"
            save_profile(path, [0, 1, 0], "hash", 15)
            original = path.read_bytes()
            worker = SpeakerWorker.__new__(SpeakerWorker)
            worker.config = SpeakerConfig(profile=path)
            worker.model_hash = "hash"
            worker.enrolled = [unit([1, 0, 0])]
            worker.enrolled_seconds = 9
            worker.finished_enrollment = False
            worker.emit = lambda **e: None
            worker.log = MemoryLog()
            worker._save_enrollment()
            self.assertEqual(path.read_bytes(), original)
            worker.enrolled_seconds = 12
            worker._save_enrollment()
            np.testing.assert_allclose(load_profile(path, "hash", 3), [1, 0, 0])

    def test_audio_offer_is_nonblocking_and_bounded(self):
        worker = SpeakerWorker.__new__(SpeakerWorker)
        worker.audio = queue.Queue(maxsize=1)
        worker.dropped = worker.highwater = 0
        samples = np.zeros(1600, np.float32)
        started = time.perf_counter()
        for i in range(1000):
            worker.feed(samples, 16000, i * .1)
        self.assertLess(time.perf_counter() - started, .5)
        self.assertEqual(worker.audio.qsize(), 1)
        self.assertEqual(worker.dropped, 999)
        self.assertEqual(worker.highwater, 1)

    def test_worker_failure_signals_ready_and_closes(self):
        log, events = MemoryLog(), []
        with patch.object(SpeakerWorker, "_load", side_effect=RuntimeError("bad model")):
            worker = SpeakerWorker(log, lambda **e: events.append(e))
            self.assertTrue(worker.ready.wait(2))
            worker.stop()
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(worker.failure, "bad model")
        self.assertTrue(any(e["kind"] == "speaker_error" for e in events))
        self.assertEqual(log.rows[-1][0], "speaker_summary")


if __name__ == "__main__":
    unittest.main()
