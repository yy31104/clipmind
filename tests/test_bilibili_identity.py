"""Multipart cache identity, using real writers and restart-loaded complete packs."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clipmind import evidence
from clipmind.asr import Segment, Transcript
from clipmind.jobs import Job, JobStore
from clipmind.links import normalize_url, source_id_from_url
from clipmind.sources import MediaAsset, adapter_for, supported_sources
from clipmind.storage import JobStorage


IDS = ("BV1fixture", "av123")
PART_CASES = (
    ("", ""), ("?p=1", "_p1"), ("?p=7", "_p7"),
    ("?p=7&utm_source=share", "_p7"), ("?p=007", "_p7"),
    ("?p=0", None), ("?p=", None), ("?p=-1", None),
    ("?p=1&p=7", None), ("?p=7&p=7", None),
    ("?p=abc", None), ("?p=7.0", None), ("?p=%207", None),
    ("?p=７", None),
)


def url(video_id: str, query: str = "") -> str:
    return f"https://www.bilibili.com/video/{video_id}{query}"


class BilibiliIdentityTests(unittest.TestCase):
    def test_request_id_truth_table_and_unchanged_canonical(self):
        for video_id in IDS:
            for query, suffix in PART_CASES:
                with self.subTest(video_id=video_id, query=query):
                    source = url(video_id, query)
                    expected = video_id + suffix if suffix is not None else None
                    self.assertEqual(source_id_from_url(source), expected)
                    self.assertEqual(adapter_for(source).name, "generic-url")
            self.assertNotEqual(normalize_url(url(video_id)), normalize_url(url(video_id, "?p=1")))
            self.assertNotEqual(normalize_url(url(video_id, "?p=1")), normalize_url(url(video_id, "?p=7")))
            self.assertEqual(normalize_url(url(video_id, "?p=7")), normalize_url(url(video_id, "?p=7&utm_source=share")))
        self.assertNotIn("bilibili", {entry["platform"] for entry in supported_sources()})

    def test_other_hosts_and_legacy_non_http_helpers_are_unchanged(self):
        for host in ("example.org", "notbilibili.com", "bilibili.com.example.org"):
            for video_id in IDS:
                for query, _ in PART_CASES:
                    with self.subTest(host=host, video_id=video_id, query=query):
                        self.assertEqual(source_id_from_url(f"https://{host}/video/{video_id}{query}"), video_id)
        self.assertEqual(source_id_from_url("ftp://bilibili.com/video/BV1fixture?p=7"), "BV1fixture")

    def test_bilibili_host_variants_have_the_same_part_identity(self):
        for host in ("bilibili.com", "WWW.BILIBILI.COM", "m.bilibili.com"):
            self.assertEqual(source_id_from_url(f"https://{host}/video/BV1fixture?p=7"), "BV1fixture_p7")


class BilibiliCompletePackReuseTests(unittest.TestCase):
    def assert_reuse(self, stored_url, media_id, requested, expected):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest = root / "synthetic-pack"
            for name in ("visual_states/all", "visual_states/preview"):
                (dest / name).mkdir(parents=True)
            # No network, ASR or OCR. The production writer creates the pack;
            # the production store reloads the durable job before deciding reuse.
            item = MediaAsset(dest / "unused.mp4", {
                "id": media_id, "webpage_url": stored_url,
                "title": "Synthetic identity fixture", "_clipmind_platform": "generic",
            })
            evidence.write_pack(dest, item, Transcript([Segment(0, 1, "Fixture")]), [], [], [], candidate_frame_count=0)
            result = {"id": media_id} if media_id is not None else {}
            job = Job(id=dest.name, url=stored_url, title=item.title, status="done", result=result)
            JobStorage(root).save(job.id, job.record())
            self.assertEqual(evidence.load_complete_pack(dest)["status"], "complete")
            before = {path.relative_to(dest): path.read_bytes() for path in dest.rglob("*") if path.is_file()}
            store = JobStore(root)
            reused = store.reusable(requested)
            self.assertEqual(reused.id if reused else None, job.id if expected else None)
            self.assertEqual(store.jobs[job.id].result, result)
            self.assertEqual(before, {path.relative_to(dest): path.read_bytes() for path in dest.rglob("*") if path.is_file()})
            (dest / "manifest.json").unlink()
            self.assertIsNone(store.reusable(requested))

    def test_whole_bare_id_cannot_satisfy_explicit_part(self):
        for video_id in IDS:
            with self.subTest(video_id=video_id):
                self.assert_reuse(url(video_id), video_id, url(video_id, "?p=7"), False)

    def test_part_matrix_through_real_reuse(self):
        for video_id in IDS:
            for query, suffix in PART_CASES:
                with self.subTest(video_id=video_id, query=query):
                    media_id = video_id + (suffix or "")
                    self.assert_reuse(url(video_id, query), media_id, url(video_id, query), suffix is not None)

    def test_distinct_parts_and_unspecified_part_are_not_inferred(self):
        for video_id in IDS:
            for stored, media_suffix, requested in (
                ("?p=1", "_p1", "?p=7"), ("?p=7", "_p7", ""),
                ("", "_p1", "?p=1"), ("?p=1", "_p1", ""),
                ("", "_p1", ""), ("?p=1", "_p7", "?p=1"),
            ):
                with self.subTest(video_id=video_id, stored=stored, requested=requested):
                    self.assert_reuse(url(video_id, stored), video_id + media_suffix, url(video_id, requested), False)

    def test_tracking_variants_reuse_same_explicit_part(self):
        for video_id in IDS:
            self.assert_reuse(url(video_id, "?p=7"), video_id + "_p7", url(video_id, "?utm_source=share&p=7"), True)
            self.assert_reuse(url(video_id, "?p=007"), video_id + "_p7", url(video_id, "?p=7"), True)

    def test_legacy_missing_or_bare_id_never_satisfies_explicit_part_even_same_url(self):
        for video_id in IDS:
            for media_id in (None, video_id):
                for stored in ("", "?p=1", "?p=7"):
                    with self.subTest(video_id=video_id, media_id=media_id, stored=stored):
                        self.assert_reuse(url(video_id, stored), media_id, url(video_id, "?p=7"), False)

    def test_bv_av_and_unrelated_hosts_are_not_aliased(self):
        # Even apparently matching downloader IDs do not prove BV/av equivalence.
        self.assert_reuse(url("av123", "?p=7"), "BV1fixture_p7", url("BV1fixture", "?p=7"), False)
        self.assert_reuse(url("BV1fixture", "?p=7"), "av123_p7", url("av123", "?p=7"), False)
        self.assert_reuse("https://other.example/video/BV1fixture?p=7", "BV1fixture_p7", url("BV1fixture", "?p=7"), False)


if __name__ == "__main__":
    unittest.main()
