"""Multiframe TIFF is ordered document evidence, not a one-frame video."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from PIL import Image

from clipmind import acquisition, compact, fetch, handoff, image_media, media, pipeline
from clipmind.config import Settings
from clipmind.storage import JobStorage


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class MultipageTiffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original = self.root / "original.tiff"
        self.pack = self.root / "pack"
        self.config = Settings(sample_width=160, evidence_width=320)
        self.providers = SimpleNamespace(
            transcript=SimpleNamespace(name="must-not-run", transcribe=AsyncMock(side_effect=AssertionError("TIFF has no speech"))),
            text=SimpleNamespace(name="fixture-ocr", read_text=lambda path: ["Synthetic page OCR"]),
            diarization=SimpleNamespace(available=lambda: False, assign=AsyncMock()),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def tiff(self, pages=None):
        pages = pages or [Image.new("RGB", (64, 48), "red"), Image.new("RGB", (48, 64), "blue")]
        pages[0].save(self.original, save_all=True, append_images=pages[1:], compression="raw")
        return pages

    async def process(self):
        with (
            patch.object(fetch, "_probe_local", side_effect=AssertionError("TIFF pages must not be probed as video")),
            patch.object(media, "sample_frames", side_effect=AssertionError("TIFF pages must not be sampled as video")),
            patch.object(media, "extract_audio", side_effect=AssertionError("TIFF has no audio")),
        ):
            result = await pipeline.process(str(self.original), self.pack, pipeline.Pools.from_settings(self.config),
                                            lambda *args: None, config=self.config, providers=self.providers)
        # JobStore normally writes this completion artifact around the pipeline.
        JobStorage(self.pack.parent).save(self.pack.name, {
            "id": self.pack.name, "url": "local:///original.tiff", "status": "done", "result": result,
        })
        return result

    async def test_all_pages_are_lossless_ordered_images_with_original_container_identity(self):
        pages = self.tiff()
        before = self.original.read_bytes()
        result = await self.process()
        source = json.loads((self.pack / "source.json").read_text(encoding="utf-8"))
        timeline = rows(self.pack / "visual_timeline.jsonl")
        self.assertEqual(source["source_id"], hashlib.sha256(before).hexdigest())
        self.assertEqual(source["media_type"], "image_collection")
        self.assertEqual(source["source_image_count"], 2)
        self.assertEqual(source["image_page_transform"], "tiff-to-png-lossless")
        self.assertEqual(result["image_page_transform"], source["image_page_transform"])
        self.assertEqual(source["time_basis"], "image_order")
        self.assertFalse(source["duration_available"])
        self.assertEqual(source["audio_status"], "not_applicable")
        self.assertEqual([row["source_image_index"] for row in timeline], [0, 1])
        self.assertEqual(len(rows(self.pack / "ocr.jsonl")), 2)
        self.assertEqual(rows(self.pack / "transcript.jsonl"), [])
        self.providers.transcript.transcribe.assert_not_awaited()
        for page, record in zip(pages, timeline):
            self.assertFalse(record["timestamp_available"])
            self.assertEqual(record["time_basis"], "image_order")
            path = self.pack / record["file"]
            self.assertEqual(path.suffix, ".png")
            with Image.open(path) as converted:
                self.assertEqual((converted.size, converted.mode, converted.tobytes()),
                                 (page.size, page.mode, page.tobytes()))
        self.assertEqual(self.original.read_bytes(), before)
        self.assertEqual(acquisition.leftovers(self.pack), [])

        # Both image-bearing handoffs retain every PNG page; text-only export
        # retains page/OCR provenance and the explicit conversion marker.
        for profile in ("full", "compact", "text"):
            output = self.root / f"{profile}.zip"
            if profile in {"compact", "text"}:
                compact.export_compact_zip(self.pack, output, image_policy="text" if profile == "text" else "content")
            else:
                handoff.export_zip(self.pack, output)
            with zipfile.ZipFile(output) as archive:
                prefix = self.pack.name + "/"
                exported_source = json.loads(archive.read(prefix + "source.json"))
                self.assertEqual(exported_source["image_page_transform"], source["image_page_transform"])
                exported_images = [name for name in archive.namelist() if "/visual_states/all/" in name and not name.endswith("/")]
                self.assertEqual(len(exported_images), 0 if profile == "text" else 2)
                if profile != "text":
                    for record in timeline:
                        self.assertEqual(archive.read(prefix + record["file"]), (self.pack / record["file"]).read_bytes())

    async def test_single_page_tiff_retains_original_bytes_without_transform(self):
        self.tiff([Image.new("RGB", (64, 48), "green")])
        before = self.original.read_bytes()
        await self.process()
        source = json.loads((self.pack / "source.json").read_text(encoding="utf-8"))
        timeline = rows(self.pack / "visual_timeline.jsonl")
        self.assertEqual(len(timeline), 1)
        self.assertNotIn("image_page_transform", source)
        self.assertEqual((self.pack / timeline[0]["file"]).read_bytes(), before)
        self.assertEqual(self.original.read_bytes(), before)

    async def test_sixteen_bit_grayscale_pages_keep_values_without_clamping(self):
        pages = [Image.new("I;16", (12, 8), value) for value in (1024, 60000)]
        self.tiff(pages)
        paths = await image_media.expand_tiff_pages(self.original, self.root / "converted")
        self.assertEqual(len(paths), 2)
        for page, path in zip(pages, paths):
            with Image.open(path) as image:
                self.assertEqual(image.getextrema(), page.getextrema())
                self.assertEqual(image.tobytes(), page.tobytes())

    async def test_page_pixel_total_pixel_and_output_byte_limits_never_return_a_prefix(self):
        self.tiff()
        original = self.original.read_bytes()
        for options in ({"max_pages": 1}, {"max_page_pixels": 100},
                        {"max_total_pixels": 4000}, {"max_output_bytes": 20}):
            with self.subTest(options=options), self.assertRaises(image_media.TiffPageLimitExceeded):
                await image_media.expand_tiff_pages(self.original, self.root / "limited", **options)
            self.assertEqual(list((self.root / "limited").iterdir()), [])
            self.assertEqual(self.original.read_bytes(), original)

    async def test_corrupt_later_page_fails_whole_job_and_cleans_owned_files(self):
        self.tiff()
        with Image.open(self.original) as image:
            image.seek(1)
            offset = image.tag_v2[273][0]
            length = image.tag_v2[279][0]
        self.original.write_bytes(self.original.read_bytes()[:offset + length // 2])
        original = self.original.read_bytes()
        with self.assertRaises(image_media.TiffPageError):
            await self.process()
        self.assertFalse((self.pack / "manifest.json").exists())
        self.assertEqual(acquisition.leftovers(self.pack), [])
        self.assertEqual(self.original.read_bytes(), original)

    async def test_unsupported_float_pages_are_refused_instead_of_lossily_converted(self):
        self.tiff([Image.new("F", (12, 8), value) for value in (1.25, 100000.75)])
        original = self.original.read_bytes()
        with self.assertRaisesRegex(image_media.TiffPageError, "losslessly"):
            await self.process()
        self.assertFalse((self.pack / "manifest.json").exists())
        self.assertEqual(acquisition.leftovers(self.pack), [])
        self.assertEqual(self.original.read_bytes(), original)

    async def test_repeated_cancellation_waits_for_page_writer_before_cleanup(self):
        self.tiff()
        original = self.original.read_bytes()
        started, exited = threading.Event(), threading.Event()

        def writer(source, directory, stop, **kwargs):
            started.set()
            try:
                stop.wait(5)
                (directory / "late-write.part").write_bytes(b"worker stopped")
            finally:
                exited.set()
            raise image_media._TiffStopped()

        with patch.object(image_media, "_decode_tiff_collection", writer):
            task = asyncio.create_task(self.process())
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(exited.is_set())
        self.assertFalse((self.pack / "manifest.json").exists())
        self.assertEqual(acquisition.leftovers(self.pack), [])
        self.assertEqual(self.original.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
