"""Pre-migration identity truth table and real complete-pack reuse behavior."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from clipmind import evidence
from clipmind.jobs import Job, JobStore
from clipmind.links import normalize_url, source_id_from_url
from clipmind.sources import SourceError, adapter_for
from tests.pack_fixture import make_complete_pack


# group, input, canonical URL, source ID, acquisition adapter
# Expected values were captured and checked against main before relocating code.
IDENTITY_CASES = [
    ("youtube", "https://youtu.be/AbC123?si=share&t=12", "https://youtube.com/watch?v=AbC123", "AbC123", "youtube"),
    ("youtube", "http://WWW.youtube.com/watch?v=AbC123&feature=share#t=3", "https://youtube.com/watch?v=AbC123", "AbC123", "youtube"),
    ("youtube", "https://m.youtube.com/watch?v=AbC123&list=playlist", "https://youtube.com/watch?v=AbC123", "AbC123", "youtube"),
    ("youtube", "https://music.youtube.com/watch?v=AbC123&index=2", "https://youtube.com/watch?v=AbC123", "AbC123", "youtube"),
    ("youtube", "https://youtube.com/shorts/AbC123?si=share", "https://youtube.com/shorts/AbC123", "AbC123", "youtube"),
    ("youtube", "https://youtube.com/live/AbC123/?feature=share", "https://youtube.com/live/AbC123", "AbC123", "youtube"),
    ("youtube", "https://youtube.com/embed/AbC123?start=4", "https://youtube.com/embed/AbC123", "AbC123", "youtube"),
    ("youtube", "https://youtube.com/watch/?v=AbC123", "https://youtube.com/watch?v=AbC123", None, "youtube"),
    ("youtube", "https://youtube.com/watch?v=A&v=B", "https://youtube.com/watch?v=B", "B", "youtube"),
    ("youtube", "https://youtube.com/watch?v=A&v=", "https://youtube.com/watch", "A", "youtube"),
    ("youtube", "https://youtube.com/watch?v=&feature=share", "https://youtube.com/watch", None, "youtube"),
    ("youtube", "https://youtube.com/watch?V=AbC123", "https://youtube.com/watch", None, "youtube"),
    ("youtube", "https://youtu.be/?si=share", "https://youtu.be/?si=share", None, "youtube"),
    ("youtube", "https://www.youtu.be/AbC123?si=share", "https://youtube.com/watch?v=AbC123", None, "youtube"),
    ("youtube", "https://studio.youtube.com/watch?v=AbC123&x=1", "https://studio.youtube.com/watch?v=AbC123&x=1", "AbC123", "youtube"),
    ("youtube", "https://foo.youtu.be/note/123?x=1", "https://foo.youtu.be/note/123?x=1", "123", "youtube"),
    ("youtube", "https://youtube.com/video/123", "https://youtube.com/video/123", None, "youtube"),
    ("douyin", "http://WWW.douyin.com/video/123/?x=tracking", "https://douyin.com/video/123", "123", "douyin"),
    ("douyin", "https://douyin.com/note/456?modal_id=999&p=7", "https://douyin.com/note/456", "456", "douyin"),
    ("douyin", "https://v.douyin.com/AbC-1/?foo=bar#fragment", "https://v.douyin.com/AbC-1", None, "douyin"),
    ("douyin", "https://www.iesdouyin.com/share/video/123/?from=share", "https://iesdouyin.com/share/video/123", "123", "douyin"),
    ("douyin", "https://iesdouyin.com/share/note/456?x=1", "https://iesdouyin.com/share/note/456", "456", "douyin"),
    ("douyin", "https://foo.iesdouyin.com/share/video/123?x=1", "https://foo.iesdouyin.com/share/video/123", "123", "douyin"),
    ("douyin", "https://douyin.com/?modal_id=123", "https://douyin.com/", None, "douyin"),
    ("douyin", "https://douyin.com/video/123x?x=1", "https://douyin.com/video/123x", None, "douyin"),
    ("generic", "http://WWW.example.org/watch/?utm_source=x&feature=y&SI=z&p=1#part", "https://example.org/watch?p=1", None, "generic-url"),
    ("generic", "https://example.org/watch?z=last&p=7&a=first", "https://example.org/watch?a=first&p=7&z=last", None, "generic-url"),
    ("generic", "https://example.org/watch?p=1", "https://example.org/watch?p=1", None, "generic-url"),
    ("generic", "https://example.org/watch?p=7", "https://example.org/watch?p=7", None, "generic-url"),
    ("generic", "https://example.org/watch?x=2&blank=&x=1&ref=keep", "https://example.org/watch?blank=&ref=keep&x=1&x=2", None, "generic-url"),
    ("generic", "https://example.org/watch?q=a%20b&unknown=a%2Fb", "https://example.org/watch?q=a+b&unknown=a%2Fb", None, "generic-url"),
    ("generic", "https://user:synthetic@example.org:8443/demo.mp4?quality=hd", "https://example.org/demo.mp4?quality=hd", None, "generic-url"),
    ("generic", "https://example.org/", "https://example.org/", None, "generic-url"),
    ("legacy", "https://www.bilibili.com/video/BV1abc123?p=7&spm_id_from=share", "https://bilibili.com/video/BV1abc123?p=7", "BV1abc123", "generic-url"),
    ("legacy", "https://www.bilibili.com/video/av123?p=1", "https://bilibili.com/video/av123?p=1", "av123", "generic-url"),
    ("legacy", "https://other.example/Bv1ABC/part?x=1", "https://other.example/Bv1ABC/part?x=1", "Bv1ABC", "generic-url"),
    ("legacy", "https://other.example/AV123/", "https://other.example/AV123", "AV123", "generic-url"),
    ("legacy", "https://other.example/video/123?x=1", "https://other.example/video/123?x=1", "123", "generic-url"),
    ("legacy", "https://other.example/share/note/456", "https://other.example/share/note/456", "456", "generic-url"),
    ("legacy", "https://notyoutube.com/watch?v=AbC123&x=1", "https://notyoutube.com/watch?v=AbC123&x=1", "AbC123", "generic-url"),
    ("legacy", "https://notdouyin.com/video/123?x=1", "https://notdouyin.com/video/123", "123", "generic-url"),
    ("legacy", "https://youtube.com.example.org/watch?v=AbC123", "https://youtube.com.example.org/watch?v=AbC123", None, "generic-url"),
    ("helper-only", "ftp://youtube.com/watch?v=AbC123&si=share", "https://youtube.com/watch?v=AbC123", "AbC123", None),
    ("helper-only", "//youtube.com/watch?v=AbC123&si=share", "https://youtube.com/watch?v=AbC123", "AbC123", None),
    ("helper-only", "file:///not-a-real-clipmind-path/demo.mp4", "https:///not-a-real-clipmind-path/demo.mp4", None, None),
    ("helper-only", "plain-text", "https:plain-text", None, None),
    ("helper-only", "", "https:///", None, None),
]


class SourceIdentityCharacterizationTests(unittest.TestCase):
    def test_existing_identity_truth_table(self) -> None:
        for group, source, canonical, source_id, adapter_name in IDENTITY_CASES:
            with self.subTest(group=group, source=source):
                self.assertEqual(normalize_url(source), canonical)
                self.assertEqual(source_id_from_url(source), source_id)
                if adapter_name is None:
                    with self.assertRaises(SourceError):
                        adapter_for(source)
                else:
                    adapter = adapter_for(source)
                    self.assertEqual(adapter.name, adapter_name)
                    self.assertEqual(adapter.canonicalize_source(source), canonical)
                    self.assertEqual(adapter.source_id(source), source_id)

    def test_all_existing_tracking_keys_are_removed_case_insensitively(self) -> None:
        for key in (
            "feature", "si", "spm_id_from", "share_source", "share_medium",
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        ):
            for spelling in (key, key.upper()):
                with self.subTest(key=spelling):
                    self.assertEqual(
                        normalize_url(f"https://example.org/watch?p=7&{spelling}=share"),
                        "https://example.org/watch?p=7",
                    )

    def test_existing_local_file_identity_including_the_file_uri_quirk(self) -> None:
        # Windows runners keep the checkout and default temp directory on
        # different drives; relative paths only exist within the same drive.
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tempdir:
            source = Path(tempdir) / "clip.mp4"
            source.write_bytes(b"synthetic media")
            resolved = source.resolve()
            for value in (
                str(source), f"{source.parent}/./{source.name}", os.path.relpath(source),
            ):
                self.assertEqual(normalize_url(value), resolved.as_uri())
                self.assertIsNone(source_id_from_url(value))
                self.assertEqual(adapter_for(value).name, "local-file")
            # The old helper handles explicit file:// differently from bare paths.
            # Correcting that behavior is outside a semantic relocation.
            self.assertEqual(
                normalize_url(resolved.as_uri()), resolved.as_uri().replace("file:", "https:", 1)
            )
            self.assertIsNone(source_id_from_url(resolved.as_uri()))
            if os.name == "nt":
                # The existing local matcher does not decode /C:/... file URIs.
                with self.assertRaises(SourceError):
                    adapter_for(resolved.as_uri())
            else:
                self.assertEqual(adapter_for(resolved.as_uri()).name, "local-file")


class SourceIdentityReuseTests(unittest.TestCase):
    def assert_reuse(
        self, stored: str, requested: str, *, source_id: str = "unresolved", expected: bool
    ) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            pack = make_complete_pack(root, source_id=source_id)
            evidence.load_complete_pack(pack)
            store = JobStore(root)
            job = Job(
                id=pack.name, url=stored, title="Fixture", status="done",
                result={"id": source_id}, finished_at=1.0,
            )
            store.jobs[job.id] = job
            self.assertIs(store.reusable(requested), job if expected else None)
            # Matching identity is insufficient if the pack is no longer complete.
            (pack / "manifest.json").unlink()
            self.assertIsNone(store.reusable(requested))

    def test_canonical_equivalence_and_tracking_only_changes_reuse_complete_packs(self) -> None:
        for stored, requested in (
            ("https://youtu.be/A?si=one", "https://youtube.com/watch?v=A&feature=two"),
            ("https://douyin.com/video/123?foo=one", "https://www.douyin.com/video/123?foo=two"),
            ("https://example.org/watch?p=7&x=1", "http://www.example.org/watch?x=1&p=7&utm_source=share"),
        ):
            with self.subTest(stored=stored, requested=requested):
                self.assert_reuse(stored, requested, expected=True)

    def test_distinct_identity_query_values_do_not_reuse_by_canonical_url(self) -> None:
        self.assert_reuse(
            "https://example.org/watch?p=1", "https://example.org/watch?p=7", expected=False
        )

    def test_known_source_id_still_reuses_across_url_forms(self) -> None:
        self.assert_reuse(
            "https://youtube.com/watch?v=A", "https://youtube.com/shorts/A",
            source_id="A", expected=True,
        )
        self.assert_reuse(
            "https://v.douyin.com/short", "https://douyin.com/video/123",
            source_id="123", expected=True,
        )

    def test_local_path_reuse_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "clip.mp4"
            source.write_bytes(b"synthetic media")
            self.assert_reuse(str(source), str(source.resolve()), expected=True)
            self.assert_reuse(str(source), source.resolve().as_uri(), expected=False)


if __name__ == "__main__":
    unittest.main()
