from __future__ import annotations

import csv
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import evaluate_real_talks as original
from scripts import review_screen_labels as review
from scripts import review_boundary_errors as error_review
from scripts import screen_evaluation as screen


class ToolProvenanceTests(unittest.TestCase):
    def test_tesseract_version_and_actual_language_bytes_without_local_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'data with spaces'
            root.mkdir()
            (root / 'eng.traineddata').write_bytes(b'English language fixture')
            (root / 'chi_tra.traineddata').write_bytes(b'Traditional Chinese fixture')
            outputs = [SimpleNamespace(stdout='tesseract 5.3.0\n leptonica-1.82.0\n', stderr=''),
                       SimpleNamespace(stdout=f'List of available languages in "{root}" (2):\nchi_tra\neng\n', stderr='')]
            with patch.object(screen.shutil, 'which', return_value='/system/tesseract'), \
                 patch.object(screen.subprocess, 'run', side_effect=outputs) as run:
                result = screen.tesseract_metadata()
            self.assertEqual(result['version'], 'tesseract 5.3.0')
            self.assertEqual(result['traineddata_sha256']['eng'], screen.sha256(root / 'eng.traineddata'))
            self.assertEqual(result['traineddata_status'], 'measured')
            self.assertEqual(run.call_count, 2)
            self.assertNotIn(temporary, json.dumps(result))

    def test_missing_tesseract_is_metadata_unavailable_without_installing_or_running(self):
        with patch.object(screen.shutil, 'which', return_value=None), patch.object(screen.subprocess, 'run') as run:
            self.assertEqual(screen.tesseract_metadata()['status'], 'unavailable')
            run.assert_not_called()

    def test_real_reader_proxy_preserves_results_exceptions_and_legacy_dispatch(self):
        value = object()
        error = RuntimeError('Vision recognition exceeded its 10-second deadline /private/source.png')
        error.add_note('Error Domain=com.apple.Vision Code=9 /private/another.png')
        calls = []
        def recognize(path):
            calls.append(path)
            if len(calls) == 2:
                raise error
            return value
        proxy = screen.OCRDiagnosticProxy(SimpleNamespace(name='apple-vision', recognize=recognize))
        self.assertIs(proxy.recognize('first'), value)
        with self.assertRaises(RuntimeError) as caught:
            proxy.recognize('second')
        self.assertIs(caught.exception, error)
        result = proxy.snapshot()
        self.assertEqual(result['started'], 2)
        self.assertEqual(result['successful'], 1)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['failure_types'][0]['category'], 'recognition_deadline')
        self.assertEqual(result['failure_types'][0]['native_error_codes'], [{'domain': 'com.apple.Vision', 'code': 9}])
        self.assertNotIn('/private/', json.dumps(result))
        legacy = screen.OCRDiagnosticProxy(SimpleNamespace(name='legacy', read_text=lambda path: ['text']))
        self.assertIsNone(getattr(legacy, 'recognize', None))
        self.assertEqual(legacy.read_text('source'), ['text'])
        self.assertEqual(legacy.snapshot()['successful'], 1)

    def test_proxy_preserves_real_annotation_failure_accounting_and_frame_results(self):
        from clipmind import visual_states
        from clipmind.media import Frame
        from clipmind.ocr import OCRBlock, OCRResult
        class Reader:
            name = 'fixture-reader'
            def recognize(self, path):
                if path.name == 'bad.jpg':
                    raise RuntimeError('recognition deadline /private/input.jpg')
                return OCRResult((OCRBlock('same text', .9, (.1, .2, .3, .4)),))
        def frames():
            return [Frame(0, 0, Path('good.jpg')), Frame(1, 1, Path('bad.jpg'))]
        plain, observed = frames(), frames()
        plain_failure = asyncio.run(visual_states.annotate(plain, asyncio.Semaphore(2), Reader()))
        proxy = screen.OCRDiagnosticProxy(Reader())
        observed_failure = asyncio.run(visual_states.annotate(observed, asyncio.Semaphore(2), proxy))
        self.assertEqual(plain_failure, observed_failure)
        self.assertEqual([(f.lines, f.ocr_layout, f.ocr_warning) for f in plain],
                         [(f.lines, f.ocr_layout, f.ocr_warning) for f in observed])
        self.assertEqual(proxy.snapshot()['started'], 2)
        self.assertEqual(proxy.snapshot()['failed'], 1)


class BoundaryInventoryTests(unittest.TestCase):
    def test_installed_runtime_byte_check_includes_web_and_refuses_extra_modules(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "web").mkdir()
            (root / "__init__.py").write_text('VERSION = "fixture"\n')
            (root / "web/app.js").write_text('const value = 1;\n')
            files = {"clipmind/__init__.py": screen.sha256(root / "__init__.py"),
                     "clipmind/web/app.js": screen.sha256(root / "web/app.js")}
            expected = {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True,
                         separators=(",", ":")).encode()).hexdigest()}
            self.assertEqual(original.verify_installed_runtime(root, expected), expected)
            (root / "web/app.js").write_text('const value = 2;\n')
            with self.assertRaisesRegex(ValueError, "do not match"):
                original.verify_installed_runtime(root, expected)
            (root / "web/app.js").write_text('const value = 1;\n')
            (root / "unexpected.py").write_text('print("untracked runtime")\n')
            with self.assertRaisesRegex(ValueError, "do not match"):
                original.verify_installed_runtime(root, expected)

    def test_exhaustive_inventory_preserves_old_matching_and_every_false_positive(self):
        states = [{"id": f"state-{n}", "start": value} for n, value in enumerate([0, 9.2, 9.7, 10.2, 20, 39.1])]
        result = screen.boundary_inventory([10, 30, 40], states, start=0, end=50)
        self.assertEqual(result["score"], original.boundary_score([10, 30, 40], [9.2, 9.7, 10.2, 20, 39.1]))
        self.assertEqual(len(result["errors"]), 4)
        self.assertEqual(sum(row["event_kind"] == "missed_change" for row in result["errors"]), 1)
        self.assertEqual(sum(row["event_kind"] == "extra_boundary" for row in result["errors"]), 3)
        self.assertTrue(all(row["status"] == "unreviewed" and not row["cause"] for row in result["errors"]))

    def test_only_explicit_non_information_states_leave_information_score(self):
        kinds = [None, "build", "content", "uncertain", "new-kind", ["bad"], "layout", "speaker", "animation"]
        states = [{"id": str(n), "start": n + 1, "change_kind": kind} for n, kind in enumerate(kinds)]
        result = screen.boundary_inventory([2, 3], states, start=0, end=20, information_only=True)
        self.assertEqual(result["score"]["prediction_count"], 6)
        self.assertEqual(result["score"]["matched"], 2)
        self.assertEqual(len(result["predictions"]), 9)
        self.assertEqual(screen.boundary_inventory([2, 3], states, start=0, end=20)["score"]["prediction_count"], 9)

    def test_attribution_cannot_claim_review_without_source_evidence(self):
        event = {"event_id": "miss-1", "status": "unreviewed"}
        self.assertEqual(screen.attribution_summary([event])["causes"], {"unreviewed": 1})
        event.update(status="reviewed", cause="progressive_build", explanation="The later line is visible")
        with self.assertRaisesRegex(ValueError, "source frame pair"):
            screen.attribution_summary([event])
        event["source_frames"] = ["before.jpg", "after.jpg"]
        self.assertEqual(screen.attribution_summary([event])["reviewed"], 1)

    def test_report_reservation_and_campaign_tokens_never_replace_an_old_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "once.json"
            screen.reserve_record(path, {"status": "failed"})
            with self.assertRaises(FileExistsError):
                screen.reserve_record(path, {"status": "success"})
            self.assertEqual(json.loads(path.read_text()), {"status": "failed"})

    def test_shareable_path_round_trip_and_escape_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = screen.safe_report({"pack_path": str(root / "case/library/pack"),
                                         "error": f"failed to read {root}/input.mp4"}, root)
            self.assertNotIn(temporary, json.dumps(record))
            self.assertEqual(screen.resolve_pack_path(record["pack_path"], root), (root / "case/library/pack").resolve())
            with self.assertRaisesRegex(ValueError, "inside"):
                screen.resolve_pack_path("<WORKDIR>/../other", root)
        self.assertEqual(screen.safe_report("version 1.4.0", Path(".")), "version 1.4.0")


class SourceReviewTests(unittest.TestCase):
    def make_inputs(self, root):
        source = root / "talk/input.mp4"
        source.parent.mkdir()
        source.write_bytes(b"source-only fixture")
        corpus = {"cases": [{"case_id": "talk", "sample_start_seconds": 100,
                              "sample_end_seconds": 1100, "title": "A real talk"}]}
        labels = {"cases": {"talk": {"review_start": 0, "review_end": 1000,
                  "input_sha256": screen.sha256(source), "slide_changes": [12, 500],
                  "boundary_notes": ['A code line is revealed.', 'A new diagram appears.'],
                  "ocr": [{"timestamp": 600, "kind": "code", "text": 'print("你好")\n</script>',
                           "roi": [0, 0, .5, 1]}]}}}
        corpus_file, labels_file = root / "corpus.json", root / "labels.json"
        corpus_file.write_text(json.dumps(corpus))
        labels_file.write_text(json.dumps(labels))
        return corpus_file, labels_file

    def test_full_talk_rows_keep_each_change_and_code_reference_without_model_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            corpus, labels = self.make_inputs(Path(temporary))
            rows = review.annotation_rows(json.loads(corpus.read_text()), json.loads(labels.read_text()))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[2]["source_seconds"], 700)
            self.assertEqual(rows[0]["description"], 'A code line is revealed.')
            self.assertTrue(all(row["decision"] == "" for row in rows))
            for row in rows:
                row["images"] = {"before": "before.jpg", "after": "after.jpg"}
            html = review.render_html(rows)
            self.assertIn("AI-assisted annotations; human review pending", html)
            self.assertNotIn('你好")\n</script>', html)
            self.assertIn("\\u003c/script>", html)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Install Pillow for source review images")
    def test_complete_source_package_csv_native_roi_and_immutable_output(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, labels = self.make_inputs(root)
            output = root / "review"
            def fake_frame(source, timestamp, target):
                Image.new("RGB", (640, 360), "white").save(target)
            with patch.object(review, "extract_frame", side_effect=fake_frame) as frames:
                result = review.build_package(corpus, labels, root, output)
                self.assertEqual(frames.call_count, 13)
                self.assertEqual([call.args[1] for call in frames.call_args_list],
                                 [11.5, 12.5, 10, 14, 499.5, 500.5, 498, 502, 599.5, 600.5, 598, 602, 600])
                with self.assertRaises(FileExistsError):
                    review.build_package(corpus, labels, root, output)
                self.assertEqual(frames.call_count, 13)
            self.assertEqual(result["status"], "ready")
            with (output / "review.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[2]["reference_text"], 'print("你好")\n</script>')
            self.assertEqual(rows[2]["decision"], "")
            self.assertEqual(rows[0]["description"], 'A code line is revealed.')
            self.assertIn('<details><summary>Show wider context', (output / 'index.html').read_text())
            with Image.open(output / "images/talk-0003-roi.png") as crop:
                self.assertEqual(crop.size, (320, 360))
            (root / "talk/input.mp4").write_bytes(b"different media")
            with self.assertRaisesRegex(ValueError, "frozen annotation bytes"):
                review.build_package(corpus, labels, root, root / "wrong-review")

    def test_failed_frame_generation_remains_a_failed_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, labels = self.make_inputs(root)
            output = root / "failed-review"
            with patch.object(review, "extract_frame", side_effect=RuntimeError("decoder failed")):
                with self.assertRaises(RuntimeError):
                    review.build_package(corpus, labels, root, output)
            self.assertEqual(json.loads((output / "manifest.json").read_text())["status"], "failed")

    def test_error_inventory_reads_all_real_pack_starts_and_checks_baseline_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / "talk/library/pack"
            pack.mkdir(parents=True)
            (pack / "visual_timeline.jsonl").write_text(''.join(json.dumps({"id": str(n), "start": t}) + '\n'
                                                                     for n, t in enumerate([0, 9.5, 10, 40])))
            labels = {"cases": {"talk": {"input_sha256": "source-digest", "review_start": 0,
                      "review_end": 60, "slide_changes": [10, 20]}}}
            report = {"cases": [{"case_id": "talk", "input_sha256": "source-digest", "pack_path": str(pack),
                                 "canonical_boundary_score": original.boundary_score([10, 20], [9.5, 10, 40])}]}
            inventory = error_review.create_inventory(report, labels, root)
            self.assertEqual(len(inventory[0]["errors"]), 3)
            self.assertTrue(all(row["event_id"].startswith("talk-") for row in inventory[0]["errors"]))
            self.assertTrue(all(row["status"] == "unreviewed" for row in inventory[0]["errors"]))
            report['cases'][0]['input_sha256'] = 'different-source'
            with self.assertRaisesRegex(ValueError, 'source does not match'):
                error_review.create_inventory(report, labels, root)


if __name__ == "__main__":
    unittest.main()
