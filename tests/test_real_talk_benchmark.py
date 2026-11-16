from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clipmind.sdk import EvidencePack
from scripts import evaluate_real_talks as benchmark
from tests.pack_fixture import make_complete_pack


class BoundaryTests(unittest.TestCase):
    def test_whole_non_slide_window_is_na_even_with_many_canonical_states(self):
        pack = SimpleNamespace(
            manifest={"completeness": {"transcript": "complete", "ocr": "complete", "visual_states": "complete"}},
            visual_timeline=[{"start": value} for value in (0, 3, 5, 179)],
            ocr=[], transcript=[],
        )
        label = {"review_start": 0, "review_end": 180, "slide_changes": [], "ocr": [],
                 "slide_metric_applicable": False, "slide_metric_reason": "speaker-only source window"}
        with tempfile.TemporaryDirectory() as temporary:
            captions = Path(temporary) / "captions.vtt"
            captions.write_text("WEBVTT\n", encoding="utf-8")
            score = benchmark.score_pack(
                pack, {"sample_start_seconds": 0, "sample_end_seconds": 180}, label, captions,
            )["canonical_boundary_score"]
        self.assertEqual(score["status"], "not_applicable")
        self.assertEqual(score["reason"], "speaker-only source window")
        self.assertEqual(score["prediction_count"], 3)
        self.assertIsNone(score["precision"])
        self.assertIsNone(score["recall"])

    def test_many_predictions_for_one_boundary_cannot_inflate_recall(self):
        score = benchmark.boundary_score([10], [9.2, 9.7, 10.2, 10.8])
        self.assertEqual(score["matched"], 1)
        self.assertEqual(score["recall"], 1)
        self.assertEqual(score["precision"], .25)

    def test_matching_is_one_to_one_and_uses_the_declared_tolerance(self):
        score = benchmark.boundary_score([2, 4, 8], [1, 3, 9.01])
        self.assertEqual(score["matches"], [[2, 1], [4, 3]])
        self.assertAlmostEqual(score["recall"], 2 / 3)

    def test_empty_references_are_not_a_perfect_score(self):
        self.assertIsNone(benchmark.boundary_score([], [])['recall'])
        self.assertIsNone(benchmark.boundary_score([], [])['precision'])
        self.assertEqual(benchmark.boundary_score([], [4])['precision'], 0)


class AnnotationGateTests(unittest.TestCase):
    def prepare(self, root):
        cases, labels = [], {}
        for identity in ("first", "second"):
            directory = root / identity
            directory.mkdir()
            (directory / 'input.mp4').write_bytes(identity.encode())
            (directory / 'captions.vtt').write_text('WEBVTT\n')
            cases.append({"case_id": identity, "sample_start_seconds": 100, "sample_end_seconds": 280})
            labels[identity] = {"reviewed": True, "input_sha256": benchmark.file_digest(directory / 'input.mp4'),
                                "review_start": 0, "review_end": 180, "slide_changes": [12], "ocr": []}
        return {"cases": cases}, {"schema_version": 1, "annotation_protocol": "source frames only", "cases": labels}

    def test_all_source_labels_are_required_before_any_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, labels = self.prepare(root)
            labels['cases']['second']['reviewed'] = False
            (root / 'corpus.json').write_text(json.dumps(corpus))
            (root / 'labels.json').write_text(json.dumps(labels))
            args = argparse.Namespace(corpus=root / 'corpus.json', labels=root / 'labels.json', workdir=root,
                                      output=root / 'report.json', asr_provider='mlx', ocr_provider='vision', mode='run')
            with patch('clipmind.sdk.ClipMind.analyze') as analyze:
                with self.assertRaisesRegex(ValueError, 'pending'):
                    benchmark.benchmark(args)
                analyze.assert_not_called()
            self.assertFalse(args.output.exists())

    def test_changed_input_cannot_reuse_old_manual_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, labels = self.prepare(root)
            (root / 'first/input.mp4').write_bytes(b'different video')
            with self.assertRaisesRegex(ValueError, 'input bytes'):
                benchmark.validate_inputs(corpus, labels, root)

    def test_publisher_caption_digest_is_checked_before_any_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, labels = self.prepare(root)
            caption = root / 'first/captions.vtt'
            (root / 'first/metadata.json').write_text(json.dumps({
                'sha256': {'captions.vtt': benchmark.file_digest(caption)}}))
            benchmark.validate_inputs(corpus, labels, root)
            caption.write_text('WEBVTT\n\nchanged reference text\n')
            with self.assertRaisesRegex(ValueError, 'captions differ from prepared source bytes'):
                benchmark.validate_inputs(corpus, labels, root)
            corpus_file, labels_file = root / 'corpus.json', root / 'labels.json'
            corpus_file.write_text(json.dumps(corpus))
            labels_file.write_text(json.dumps(labels))
            args = argparse.Namespace(corpus=corpus_file, labels=labels_file, workdir=root,
                                      output=root / 'report.json', asr_provider='mlx', ocr_provider='vision', mode='run')
            with patch('clipmind.sdk.ClipMind.analyze') as analyze:
                with self.assertRaisesRegex(ValueError, 'captions differ from prepared source bytes'):
                    benchmark.benchmark(args)
                analyze.assert_not_called()

    def test_source_gate_does_not_infer_labels_from_existing_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, labels = self.prepare(root)
            (root / 'first/library').mkdir()
            (root / 'first/library/visual_timeline.jsonl').write_text('not source annotations')
            benchmark.validate_inputs(corpus, labels, root)
            self.assertEqual(labels['cases']['first']['slide_changes'], [12])

    def test_malformed_rois_and_checkout_workdir_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, labels = self.prepare(root)
            labels['cases']['first']['ocr'] = [{"timestamp": 10, "roi": [.8, 0, .5, 1], "text": 'x'}]
            with self.assertRaisesRegex(ValueError, 'ROI'):
                benchmark.validate_inputs(corpus, labels, root)
            with self.assertRaisesRegex(ValueError, 'outside'):
                benchmark.validate_inputs(corpus, labels, benchmark.REPO / 'out')


class CaptionParsingTests(unittest.TestCase):
    def test_vtt_tags_entities_and_cue_settings_are_parsed(self):
        cues = benchmark.vtt_cues('WEBVTT\n\n7\n00:01:40.000 --> 00:01:42.500 align:start\n<v Ada>Hello &amp; world</v>\n')
        self.assertEqual(cues, [{"start": 100, "end": 102.5, "text": 'Hello & world'}])

    def test_roi_uses_normalized_block_centers_and_preserves_reading_order(self):
        layout = [{"text": 'outside', "bbox": [.8, 0, .1, .1]},
                  {"text": 'first', "bbox": [.1, .1, .1, .1]},
                  {"text": 'second', "bbox": [.2, .2, .1, .1]},
                  {"text": 'unknown layout'}]
        self.assertEqual(benchmark.roi_text(layout, [0, 0, .5, .5]), 'first second')

    def test_runtime_fingerprint_hashes_actual_uncommitted_python_and_web_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'clipmind/web').mkdir(parents=True)
            python = root / 'clipmind/__init__.py'
            python.write_text('__version__ = "1.3.0"\n')
            (root / 'clipmind/web/app.js').write_text('const version = 1;\n')
            tracked = b'clipmind/__init__.py\0clipmind/web/app.js\0'
            with patch.object(benchmark, 'REPO', root), patch.object(benchmark.subprocess, 'check_output', return_value=tracked):
                original = benchmark.runtime_code_fingerprint()
                python.write_text('__version__ = "1.3.1"\n')
                changed = benchmark.runtime_code_fingerprint()
            self.assertNotEqual(original['sha256'], changed['sha256'])
            self.assertEqual(set(changed['files']), {'clipmind/__init__.py', 'clipmind/web/app.js'})


@unittest.skipUnless(importlib.util.find_spec('jiwer'), 'Install scripts/requirements-eval.txt for WER/CER tests')
class RealMetricTests(unittest.TestCase):
    def test_rescore_accepts_production_local_sha_id_and_records_a_mismatch_as_failure(self):
        self.enterContext(patch.object(benchmark, 'tool_versions', return_value={'ffmpeg': 'test fixture'}))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'talk/input.mp4'
            source.parent.mkdir()
            source.write_bytes(b'prepared source bytes')
            (source.parent / 'captions.vtt').write_text('WEBVTT\n\n00:01:41.000 --> 00:01:44.000\nvector retrieval pipeline\n')
            corpus = {"cases": [{"case_id": 'talk', "title": 'Talk', "page_url": 'https://example.org/talk',
                                  "sample_start_seconds": 100, "sample_end_seconds": 280}]}
            labels = {"schema_version": 1, "annotation_protocol": 'independent source-frame review', "cases": {
                "talk": {"reviewed": True, "input_sha256": benchmark.file_digest(source), "review_start": 0,
                         "review_end": 180, "slide_changes": [], "ocr": []}}}
            corpus_file, labels_file, output = root / 'corpus.json', root / 'labels.json', root / 'report.json'
            corpus_file.write_text(json.dumps(corpus))
            labels_file.write_text(json.dumps(labels))
            pack = make_complete_pack(source.parent / 'library')
            manifest = json.loads((pack / 'manifest.json').read_text())
            manifest['source'] = {"platform": 'local', "id": benchmark.file_digest(source)}
            (pack / 'manifest.json').write_text(json.dumps(manifest))
            previous = {"labels_sha256": benchmark.file_digest(labels_file), "corpus_sha256": benchmark.file_digest(corpus_file),
                        "cases": [{"case_id": 'talk', "pack_path": str(pack), "wall_seconds": 42,
                                   "captions_sha256": benchmark.file_digest(source.parent / 'captions.vtt')}]}
            output.write_text(json.dumps(previous))
            args = argparse.Namespace(corpus=corpus_file, labels=labels_file, workdir=root, output=output,
                                      asr_provider='mlx', ocr_provider='vision', mode='score')
            result = benchmark.benchmark(args)
            self.assertEqual(result['status'], 'measured')
            self.assertEqual(result['cases'][0]['wall_seconds'], 42)
            self.assertEqual(result['cases'][0]['publisher_caption_agreement']['value'], 0)
            manifest['source']['id'] = 'some other video'
            (pack / 'manifest.json').write_text(json.dumps(manifest))
            result = benchmark.benchmark(args)
            self.assertEqual(result['status'], 'has_failures')
            self.assertEqual(result['cases'][0]['status'], 'failed')
            self.assertIn('does not match', result['cases'][0]['error'])
            (source.parent / 'captions.vtt').write_text('WEBVTT\n\nchanged reference\n')
            with self.assertRaisesRegex(ValueError, 'unchanged publisher captions'):
                benchmark.benchmark(args)

    def test_known_word_and_case_sensitive_character_error_rates(self):
        self.assertAlmostEqual(benchmark.edit_score('one two three', 'one three', speech=True)['value'], 1 / 3)
        self.assertEqual(benchmark.edit_score('Ａ B\nC', 'AbC')['value'], 1 / 3)
        self.assertEqual(benchmark.edit_score('Hello, WORLD!', 'hello world', speech=True)['value'], 0)

    def test_empty_truth_is_na_and_empty_asr_is_not_a_false_zero(self):
        self.assertIsNone(benchmark.edit_score('', '')['value'])
        self.assertEqual(benchmark.edit_score('two words', '', speech=True)['value'], 1)
        cues = [{"start": 1, "end": 3, "text": 'two words'}]
        self.assertEqual(benchmark.caption_score(cues, [], 0, 10, 'complete')['value'], 1)
        failed = benchmark.caption_score(cues, [], 0, 10, 'unavailable')
        self.assertEqual(failed['status'], 'failed')
        self.assertIsNone(failed['value'])

    def test_only_fully_contained_caption_cues_and_asr_words_are_compared(self):
        cues = [{"start": 99, "end": 101, "text": 'partial leading'},
                {"start": 102, "end": 104, "text": 'hello world'},
                {"start": 109, "end": 111, "text": 'partial trailing'}]
        transcript = [{"start": 0, "end": 8, "text": 'wrong broad segment', "words": [
            {"start": 0, "end": 1, "text": 'unrelated'},
            {"start": 2, "end": 3, "text": 'hello'},
            {"start": 3, "end": 4, "text": 'world'},
            {"start": 5, "end": 6, "text": 'unrelated'}]}]
        score = benchmark.caption_score(cues, transcript, 100, 110, 'complete')
        self.assertEqual(score['value'], 0)
        self.assertEqual(score['clip_interval'], [2, 4])
        self.assertEqual(score['publisher_cue_count'], 1)
        self.assertEqual(benchmark.caption_score(cues, [{"start": 0, "end": 8, "text": 'hello world'}],
                                               100, 110, 'complete')['value'], 1)

    def test_decimal_caption_offsets_do_not_drop_exact_boundary_words(self):
        cues = [{"start": 102.2, "end": 104.2, "text": 'hello world'}]
        transcript = [{"start": 0, "end": 8, "words": [
            {"start": 2.2, "end": 3, "text": 'hello'},
            {"start": 3, "end": 4.2, "text": 'world'}]}]
        score = benchmark.caption_score(cues, transcript, 100, 110, 'complete')
        self.assertEqual(score['value'], 0)
        self.assertEqual(score['clip_interval'], [2.2, 4.2])

    def test_ocr_scores_the_frame_returned_by_the_actual_sdk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = make_complete_pack(Path(temporary))
            timeline = [{"id": 'earlier', "start": 0, "end": 5, "file": 'visual_states/all/early.jpg'},
                        {"id": 'covering', "start": 5, "end": 10, "file": 'visual_states/all/late.jpg'}]
            records = [{"visual_state_ref": 'earlier', "layout": [{"text": 'WRONG', "bbox": [0, 0, .2, .2]}]},
                       {"visual_state_ref": 'covering', "layout": [{"text": 'code()', "bbox": [.1, .1, .2, .2]},
                                                                {"text": 'irrelevant', "bbox": [.8, .1, .1, .1]}]}]
            for filename, rows in [('visual_timeline.jsonl', timeline), ('ocr.jsonl', records)]:
                (root / filename).write_text(''.join(json.dumps(row) + '\n' for row in rows))
            for state in timeline:
                (root / state['file']).write_bytes(b'image-presence-fixture')
            captions = Path(temporary) / 'captions.vtt'
            captions.write_text('WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nvector retrieval pipeline\n')
            label = {"review_start": 0, "review_end": 10, "slide_changes": [5],
                     "ocr": [{"timestamp": 6, "roi": [0, 0, .5, .5], "text": 'code()', "kind": 'code'}]}
            case = {"sample_start_seconds": 0, "sample_end_seconds": 10}
            result = benchmark.score_pack(EvidencePack.open(root), case, label, captions)
            self.assertEqual(result['roi_ocr_cer'], 0)
            self.assertEqual(result['ocr_references'][0]['visual_state_id'], 'covering')
            self.assertEqual(result['canonical_boundary_score']['matched'], 1)
            label['ocr'] = []
            self.assertIsNone(benchmark.score_pack(EvidencePack.open(root), case, label, captions)['roi_ocr_cer'])


if __name__ == '__main__':
    unittest.main()
