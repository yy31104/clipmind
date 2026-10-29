from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind.evidence import EvidencePackError
from clipmind import sdk
from clipmind.jobs import Job, JobStore
from clipmind.sdk import ClipMind, ClipMindError, EvidencePack, PackLibrary
from tests.pack_fixture import make_complete_pack


class EvidencePackSDKTests(unittest.TestCase):
    def test_library_exposes_only_complete_packs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            make_complete_pack(root)
            (root / "partial").mkdir()
            library = PackLibrary(root)

            packs = library.list()
            summary = library.get("pack-1").summary()

        self.assertEqual([pack.id for pack in packs], ["pack-1"])
        self.assertEqual(summary["source_id"], "video-1")
        self.assertEqual(summary["counts"]["canonical_visual_states"], 1)

    def test_pack_search_and_timestamp_frame_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pack = EvidencePack.open(make_complete_pack(Path(tempdir)))

            speech = pack.search("pipeline")
            visual = pack.search("向量")
            frame, record = pack.frame(timestamp=3.0)

        self.assertEqual(speech[0]["kind"], "transcript")
        self.assertEqual(visual[0]["kind"], "ocr")
        self.assertEqual(record["id"], "visual-00001")
        self.assertEqual(frame.name, "00-02-00001.jpg")

    def test_pack_id_and_frame_paths_cannot_escape_the_library(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = EvidencePack.open(make_complete_pack(root))
            timeline = pack.root / "visual_timeline.jsonl"
            timeline.write_text(
                '{"id":"visual-00001","start":0,"end":1,"file":"../../outside.jpg"}\n',
                encoding="utf-8",
            )

            with self.assertRaises(EvidencePackError):
                PackLibrary(root).get("../pack-1")
            with self.assertRaises(EvidencePackError):
                pack.frame(visual_state_id="visual-00001")

    def test_bad_encoding_and_deep_json_report_pack_errors(self) -> None:
        # The outer object plus these arrays exceeds our own record limit,
        # regardless of the interpreter's native JSON recursion threshold.
        depth = sdk.MAX_RECORD_JSON_DEPTH
        malformed = (b"\xff", ('{"nested":' + '[' * depth
                               + '0' + ']' * depth + '}').encode(),
                     b'{"missing":}', b'{"unclosed":"value')
        for attribute, filename in (("source", "source.json"),
                                    ("transcript", "transcript.jsonl"),
                                    ("ocr", "ocr.jsonl"),
                                    ("visual_timeline", "visual_timeline.jsonl")):
            for payload in malformed:
                with self.subTest(attribute=attribute, encoding_error=payload == b"\xff"), tempfile.TemporaryDirectory() as tempdir:
                    pack = EvidencePack.open(make_complete_pack(Path(tempdir)))
                    (pack.root / filename).write_bytes(payload)
                    with self.assertRaisesRegex(EvidencePackError, filename):
                        getattr(pack, attribute)

    def test_json_nesting_boundary_accepts_valid_records_and_ignores_escaped_text(self) -> None:
        depth = sdk.MAX_RECORD_JSON_DEPTH - 1
        at_limit = '{"nested":' + '[' * depth + '0' + ']' * depth + '}'
        # Braces, quotes and backslashes are ordinary evidence text when
        # encoded inside a JSON string, including Unicode escape sequences.
        text_record = json.dumps({"text": ('[{\\\"quoted\"}]}\\' * 500) + '\u0022[\u005c'})
        unicode_escaped = r'{"text":"\u0022[\u005c\u0022{"}'
        for attribute, filename in (("source", "source.json"),
                                    ("transcript", "transcript.jsonl"),
                                    ("ocr", "ocr.jsonl"),
                                    ("visual_timeline", "visual_timeline.jsonl")):
            with self.subTest(attribute=attribute), tempfile.TemporaryDirectory() as tempdir:
                pack = EvidencePack.open(make_complete_pack(Path(tempdir)))
                for payload in (at_limit, text_record, unicode_escaped):
                    (pack.root / filename).write_text(payload, encoding="utf-8")
                    expected = json.loads(payload)
                    self.assertEqual(getattr(pack, attribute),
                                     expected if attribute == "source" else [expected])

    def test_bad_source_metadata_does_not_hide_healthy_library(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            make_complete_pack(root, name="healthy")
            broken = make_complete_pack(root, name="broken")
            (broken / "source.json").write_bytes(b"\xff")
            library = PackLibrary(root)
            self.assertEqual([pack.summary()["pack_id"] for pack in library.list()], ["healthy"])
            with self.assertRaisesRegex(EvidencePackError, "source.json"):
                library.get("broken").summary()
            self.assertEqual((broken / "source.json").read_bytes(), b"\xff")


class SDKResyncTests(unittest.IsolatedAsyncioTestCase):
    async def run_burst(self, *, failed=False):
        """Use the real 256-event broadcast, but no acquisition or disk writes."""
        instances = []

        class BurstStore:
            subscribe = JobStore.subscribe
            unsubscribe = JobStore.unsubscribe
            _publish = JobStore._publish

            def __init__(self, *args, **kwargs):
                self.jobs = {}
                self._subscribers = set()
                self.submitted = []
                self.closed = False
                instances.append(self)

            def reusable(self, url):
                return None

            def submit(self, url, title, *, options=None):
                index = len(self.submitted)
                self.submitted.append(url)
                job = Job(str(index), url, title, status="done")
                if failed and index == 128:
                    job.status = "error"
                    job.error = "The fixture source is unavailable."
                    job.error_code = "fixture_unavailable"
                self.jobs[job.id] = job
                self._publish(job)
                return job

            async def close(self):
                self.closed = True

        sources = [f"https://youtu.be/fixture-{i}" for i in range(257)]
        progress = []
        with tempfile.TemporaryDirectory() as tempdir:
            client = ClipMind(tempdir)
            with patch.object(sdk, "JobStore", BurstStore), \
                 patch.object(sdk, "extract_sources", return_value=sources), \
                 patch.object(client.library, "get", side_effect=lambda identity: SimpleNamespace(id=identity)):
                if failed:
                    with self.assertRaises(ClipMindError) as raised:
                        await asyncio.wait_for(client.analyze("fixture batch", progress=progress.append), timeout=2)
                    result = raised.exception
                else:
                    result = await asyncio.wait_for(client.analyze("fixture batch", progress=progress.append), timeout=2)
        store = instances[0]
        self.assertTrue(store.closed)
        self.assertFalse(store._subscribers)
        self.assertEqual(store.submitted, sources)
        self.assertEqual(len(progress), 257)
        return result

    async def test_overflow_recovers_all_completed_jobs_in_input_order(self):
        result = await self.run_burst()
        self.assertEqual([pack.id for pack in result], [str(i) for i in range(257)])

    async def test_overflow_preserves_terminal_failure_instead_of_hanging(self):
        error = await self.run_burst(failed=True)
        self.assertEqual(error.code, "fixture_unavailable")
        self.assertIn("fixture source", str(error))


if __name__ == "__main__":
    unittest.main()
