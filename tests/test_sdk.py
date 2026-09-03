from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clipmind.evidence import EvidencePackError
from clipmind.sdk import EvidencePack, PackLibrary
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


if __name__ == "__main__":
    unittest.main()
