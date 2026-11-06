"""Exercise evaluation-to-production wiring without loading a speech model."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from clipmind import asr
from scripts import evaluate_noisy_slides as evaluation


class NoisySlidesEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.wav = self.root / "control.wav"
        self.wav.write_bytes(b"test audio")
        self.calls = []
        self.consumed = []
        self.loads = []
        asr._faster_models.clear()
        self.addCleanup(asr._faster_models.clear)

    def _model_module(
        self, *, error: bool = False, empty_control: bool = False,
        error_noise_baseline: bool = False,
        control_text_by_variant: dict | None = None,
    ):
        owner = self

        class WhisperModel:
            def __init__(self, name, **kwargs):
                owner.loads.append((name, kwargs))
                self.model = types.SimpleNamespace(device="cpu", compute_type="int8")

            def transcribe(self, audio, **kwargs):
                owner.calls.append((Path(audio).name, kwargs))
                if error or (
                    error_noise_baseline and "noise" in Path(audio).name
                    and not kwargs["vad_filter"]
                ):
                    raise RuntimeError("injected decoder failure")

                def segments():
                    owner.consumed.append(Path(audio).name)
                    if "noise" not in Path(audio).name and not empty_control:
                        name = (
                            "guards_off" if not kwargs["vad_filter"]
                            else "vad_only" if kwargs["hallucination_silence_threshold"] is None
                            else "default_guards"
                        )
                        yield types.SimpleNamespace(
                            start=4.0, end=9.5,
                            text=(control_text_by_variant or {}).get(name, evaluation.SPEECH_TEXT),
                            words=[],
                        )

                return segments(), types.SimpleNamespace(language="en")

        module = types.ModuleType("faster_whisper")
        module.WhisperModel = WhisperModel
        module.__version__ = "test-stub"
        return module

    def _provider_boundary(self, **kwargs):
        stack = ExitStack()
        stack.enter_context(patch.dict(sys.modules, {"faster_whisper": self._model_module(**kwargs)}))
        stack.enter_context(patch.object(asr, "faster_whisper_available", return_value=True))
        return stack

    async def test_three_variants_use_the_production_provider_and_consume_segments(self):
        with self._provider_boundary():
            result = await evaluation._decode_variants(self.wav, "local-test-model")

        self.assertEqual(list(result), ["guards_off", "vad_only", "default_guards"])
        self.assertEqual(len(self.loads), 1, "the shipping provider shares its model cache")
        self.assertEqual(self.consumed, [self.wav.name] * 3)
        self.assertEqual(
            [(call[1]["vad_filter"], call[1]["hallucination_silence_threshold"]) for call in self.calls],
            [(False, None), (True, None), (True, 2.0)],
        )
        for _, kwargs in self.calls:
            self.assertEqual(kwargs["language"], "en")
            self.assertTrue(kwargs["word_timestamps"])
            self.assertFalse(kwargs["condition_on_previous_text"])
        for row in result.values():
            self.assertEqual(row["engine"], "faster-whisper")
            self.assertEqual(row["text"], evaluation.SPEECH_TEXT)
            self.assertEqual(row["segments"][0]["start"], 4.0)
            self.assertGreaterEqual(row["elapsed_seconds"], 0)
        json.dumps(result)

    async def test_decode_raises_a_provider_error(self):
        with self._provider_boundary(error=True):
            with self.assertRaisesRegex(RuntimeError, "Whisper evaluation failed"):
                await evaluation._decode(self.wav, evaluation.Settings())

    async def test_failed_variants_are_retained_without_stopping_the_comparison(self):
        with self._provider_boundary(error=True):
            result = await evaluation._decode_variants(self.wav, "local-test-model")
        self.assertEqual(len(self.calls), 3)
        for row in result.values():
            self.assertEqual(row["status"], "error")
            self.assertIn("Whisper evaluation failed", row["error"])
            self.assertIsNone(row["text"], "an error is not an empty transcript")
            self.assertEqual(row["segments"], [])

    def _fixture_boundary(self):
        stack = ExitStack()
        stack.enter_context(patch.object(evaluation, "build_sample_video", return_value=self.root / "slides.mp4"))
        stack.enter_context(patch.object(evaluation, "_noise_bed"))
        stack.enter_context(patch.object(evaluation, "_speech_over_bed"))
        stack.enter_context(patch.object(evaluation, "_mux"))

        async def extract_audio(source, destination):
            destination.write_bytes(Path(source).name.encode())
            return destination

        stack.enter_context(patch.object(evaluation.media, "extract_audio", side_effect=extract_audio))
        stack.enter_context(patch.object(evaluation, "_speech_chunks", side_effect=[
            ([], 0.0, -38.0),
            ([{"start": 3.6, "end": 9.5}], 5.9, -30.0),
        ]))
        return stack

    async def test_full_decode_evaluation_checks_both_cases_and_records_metadata(self):
        with self._fixture_boundary(), self._provider_boundary():
            report = await evaluation.evaluate(True, False, "local-test-model")

        self.assertTrue(report["passed"])
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(report["model_decoding"], "production provider run")
        self.assertEqual(report["model"]["provider_entry"], "clipmind.asr.FasterWhisperProvider.transcribe")
        self.assertEqual(report["model"]["requested_model"], "local-test-model")
        self.assertEqual(report["model"]["device"], "cpu")
        for case in report["cases"].values():
            self.assertEqual(len(case["audio_sha256"]), 64)
        self.assertNotEqual(
            report["cases"]["noise"]["audio_sha256"],
            report["cases"]["control"]["audio_sha256"],
        )
        # A clean guards-off baseline is a legitimate result, not a failure.
        self.assertEqual(report["cases"]["noise"]["decoding"]["guards_off"]["text"], "")

    async def test_baseline_error_is_visible_and_keeps_the_overall_gate_red(self):
        with self._fixture_boundary(), self._provider_boundary(error_noise_baseline=True):
            report = await evaluation.evaluate(True, False, "local-test-model")

        self.assertEqual(len(self.calls), 6, "all cases and settings must still be attempted")
        self.assertFalse(report["passed"])
        self.assertFalse(report["all_decodes_succeeded"])
        self.assertTrue(report["guard_checks_passed"])
        self.assertEqual(len(report["decode_errors"]), 1)
        self.assertEqual(report["decode_errors"][0]["case"], "noise")
        self.assertEqual(report["decode_errors"][0]["variant"], "guards_off")
        self.assertEqual(report["cases"]["noise"]["decoding"]["guards_off"]["status"], "error")

    async def test_a_default_noise_error_is_not_a_passing_empty_transcript(self):
        with self._fixture_boundary(), self._provider_boundary(error=True):
            report = await evaluation.evaluate(True, False, "local-test-model")

        self.assertEqual(len(self.calls), 6)
        self.assertFalse(report["passed"])
        self.assertFalse(report["guard_checks_passed"])
        self.assertFalse(report["checks"]["decoded_noise_transcript_empty_with_defaults"])
        self.assertEqual(len(report["decode_errors"]), 6)

    async def test_empty_speech_control_fails_the_evaluation(self):
        with self._fixture_boundary(), self._provider_boundary(empty_control=True):
            report = await evaluation.evaluate(True, False, "local-test-model")

        self.assertFalse(report["passed"])
        for name, _, _ in evaluation.DECODE_VARIANTS:
            self.assertFalse(report["checks"][f"{name}_retains_control_speech"])
            self.assertFalse(report["checks"][f"{name}_retains_control_timing"])
            if name != "guards_off":
                self.assertFalse(report["checks"][f"{name}_control_word_coverage_not_worse_than_baseline"])

    async def test_baseline_word_errors_are_measured_without_requiring_a_perfect_baseline(self):
        with self._fixture_boundary(), self._provider_boundary(
            control_text_by_variant={"guards_off": "Alpha plan. Beta system. Wrong words."},
        ):
            report = await evaluation.evaluate(True, False, "local-test-model")

        self.assertTrue(report["passed"])
        baseline = report["measurements"]["control_word_coverage"]["guards_off"]
        self.assertFalse(baseline["all_expected_words_present"])
        self.assertEqual(baseline["missing_expected_words"], ["data", "delta", "gamma", "review"])
        self.assertEqual(baseline["expected_unique_word_coverage"], 0.5)
        self.assertNotIn("guards_off_retains_control_words", report["checks"])
        self.assertTrue(report["checks"]["guards_off_retains_control_speech"])

    async def test_guarded_word_coverage_regression_still_fails_the_gate(self):
        with self._fixture_boundary(), self._provider_boundary(
            control_text_by_variant={"default_guards": "Alpha plan. Beta system. Wrong words."},
        ):
            report = await evaluation.evaluate(True, False, "local-test-model")

        self.assertFalse(report["passed"])
        self.assertFalse(report["checks"]["default_guards_control_word_coverage_not_worse_than_baseline"])
        self.assertTrue(report["all_decodes_succeeded"])

    async def test_paired_improvement_passes_but_incorrect_word_remains_visible(self):
        with self._fixture_boundary(), self._provider_boundary(control_text_by_variant={
            "guards_off": "Alpha Plan, Beta System, Camonator, Delta Renew",
            "vad_only": "AlphaPlan, BetaSystem, GammaNator, DeltaReview",
            "default_guards": "AlphaPlan, BetaSystem, GammaNator, DeltaReview",
        }):
            report = await evaluation.evaluate(True, False, "local-test-model")

        self.assertTrue(report["passed"])
        measured = report["measurements"]["control_word_coverage"]
        self.assertEqual(measured["guards_off"]["expected_unique_word_coverage"], 0.625)
        for name in ("vad_only", "default_guards"):
            self.assertEqual(measured[name]["expected_unique_word_coverage"], 0.875)
            self.assertEqual(measured[name]["missing_expected_words"], ["data"])
            self.assertFalse(measured[name]["all_expected_words_present"])

    async def test_all_wrong_guarded_words_fail_even_if_the_baseline_is_also_wrong(self):
        with self._fixture_boundary(), self._provider_boundary(control_text_by_variant={
            name: "Completely unrelated sentence" for name, _, _ in evaluation.DECODE_VARIANTS
        }):
            report = await evaluation.evaluate(True, False, "local-test-model")

        self.assertFalse(report["passed"])
        for name in ("vad_only", "default_guards"):
            self.assertFalse(report["checks"][f"{name}_control_word_coverage_not_worse_than_baseline"])

    async def test_invented_noise_text_fails_even_when_the_control_is_retained(self):
        with self._fixture_boundary(), self._provider_boundary():
            report = await evaluation.evaluate(True, False, "local-test-model")
        report["cases"]["noise"]["decoding"]["default_guards"]["text"] = "invented words"
        self.assertFalse(
            evaluation._decode_checks(report["cases"])["decoded_noise_transcript_empty_with_defaults"]
        )

    async def test_control_words_at_the_wrong_time_do_not_pass(self):
        with self._fixture_boundary(), self._provider_boundary():
            report = await evaluation.evaluate(True, False, "local-test-model")
        control = report["cases"]["control"]["decoding"]["default_guards"]
        control["segments"] = [{"start": 20.0, "end": 25.0, "text": evaluation.SPEECH_TEXT}]
        checks = evaluation._decode_checks(report["cases"])
        self.assertTrue(checks["default_guards_control_word_coverage_not_worse_than_baseline"])
        self.assertFalse(checks["default_guards_retains_control_timing"])

    async def test_vad_only_does_not_claim_or_attempt_model_decoding(self):
        with self._fixture_boundary(), patch.object(evaluation, "_decode_variants") as decode:
            report = await evaluation.evaluate(False, False)

        self.assertTrue(report["passed"])
        decode.assert_not_called()
        self.assertEqual(report["model_decoding"], "not run (VAD stage only)")
        self.assertIsNone(report["model"])

    def test_noise_generation_is_seeded(self):
        with patch.object(evaluation, "_run") as run:
            evaluation._noise_bed(self.root / "noise.wav")
        command = run.call_args.args[0]
        self.assertTrue(any(f"seed={evaluation.NOISE_SEED}" in part for part in command))

    def test_local_model_assets_are_identified_without_loading_a_model(self):
        model = self.root / "snapshots" / "fixed-revision"
        model.mkdir(parents=True)
        (model / "config.json").write_bytes(b"{}")
        (model / "model.bin").write_bytes(b"local weights")
        metadata = evaluation._model_metadata(str(model))
        self.assertEqual(metadata["local_snapshot_revision"], "fixed-revision")
        self.assertEqual(set(metadata["local_model_sha256"]), {"model.bin", "config.json"})
        self.assertTrue(all(len(value) == 64 for value in metadata["local_model_sha256"].values()))
        self.assertIsNone(metadata["device"])

    def test_words_split_observed_case_boundaries_but_do_not_repair_misspellings(self):
        self.assertEqual(evaluation._words("AlphaPlan BetaSystem GammaNator DeltaReview"), {
            "alpha", "plan", "beta", "system", "gamma", "nator", "delta", "review",
        })
        self.assertNotIn("data", evaluation._words("GammaNator"))
        self.assertNotIn("gamma", evaluation._words("Camonator"))

    def test_dependency_provenance_records_only_the_public_upstream_archive(self):
        digest = "a" * 64
        direct = {
            "url": "https://github.com/SYSTRAN/faster-whisper/archive/fixed.zip?token=secret",
            "archive_info": {"hashes": {"sha256": digest}},
        }
        distribution = types.SimpleNamespace(read_text=lambda _: json.dumps(direct))
        with self._provider_boundary(), patch.object(evaluation.metadata, "distribution", return_value=distribution):
            provenance = evaluation._decoder_provenance()
        self.assertEqual(provenance["archive_url"], "https://github.com/SYSTRAN/faster-whisper/archive/fixed.zip")
        self.assertEqual(provenance["archive_sha256"], digest)
        direct["url"] = "file:///private/personal/model-install"
        with self._provider_boundary(), patch.object(evaluation.metadata, "distribution", return_value=distribution):
            provenance = evaluation._decoder_provenance()
        self.assertIsNone(provenance["archive_url"])
        self.assertIsNone(provenance["archive_sha256"])


if __name__ == "__main__":
    unittest.main()
