import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from clipmind import ocr, visual_states
from clipmind.config import Settings
from clipmind.media import Frame


class OCRCancellationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        ocr._close_vision_worker()

    async def _cancel_native_request(self, repeated):
        ocr._close_vision_worker()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            image = folder / "source.png"
            original = Image.new("RGB", (160, 80), "black")
            ImageDraw.Draw(original).rectangle((10, 10, 30, 20), fill="white")
            original.save(image)
            source_bytes = image.read_bytes()
            request_log, finished = folder / "request.json", folder / "finished.json"
            command = [sys.executable, "-u", "-c", """import json,pathlib,sys,time
for line in sys.stdin:
    request=json.loads(line)
    pathlib.Path(sys.argv[1]).write_text(json.dumps(request))
    time.sleep(.4)
    pathlib.Path(sys.argv[2]).write_text(json.dumps({'image_exists':pathlib.Path(request['image']).exists()}))
    print(json.dumps({'status':'ok','blocks':[]}),flush=True)
""", str(request_log), str(finished)]
            with patch.object(ocr, "_vision_worker_command", return_value=command):
                task = asyncio.create_task(visual_states.annotate(
                    [Frame(index=0, timestamp=0, path=image)],
                    asyncio.Semaphore(1), ocr.VisionTextRecognizer(Settings()),
                ))
                deadline = time.monotonic() + 3
                while not request_log.exists() and not task.done() and time.monotonic() < deadline:
                    await asyncio.sleep(.01)
                self.assertTrue(request_log.exists(), "controlled child never received the request")
                prepared = Path(json.loads(request_log.read_text())["image"])
                self.assertNotEqual(prepared, image)
                self.assertTrue(prepared.exists())
                started = time.monotonic()
                task.cancel()
                await asyncio.sleep(.03)
                self.assertFalse(task.done())
                if repeated:
                    task.cancel()
                    await asyncio.sleep(.03)
                    self.assertFalse(task.done())
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=3)
                self.assertGreater(time.monotonic() - started, .2)
                self.assertEqual(json.loads(finished.read_text()), {"image_exists": True})
                self.assertFalse(prepared.exists())
                self.assertFalse(prepared.parent.exists())
                self.assertEqual(image.read_bytes(), source_bytes)
                self.assertFalse(ocr._worker_lock.locked())

    async def test_cancellation_waits_for_native_request_and_temporary_image_cleanup(self):
        await self._cancel_native_request(repeated=False)

    async def test_repeated_cancellation_cannot_skip_native_request_cleanup(self):
        await self._cancel_native_request(repeated=True)


class PortableOCRDeadlineTests(unittest.TestCase):
    def test_tesseract_call_has_a_finite_deadline(self):
        seen = {}

        def recognize(image, **kwargs):
            seen.update(kwargs)
            return {"text": []}

        fake = SimpleNamespace(image_to_data=recognize, Output=SimpleNamespace(DICT="dict"))
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "source.png"
            Image.new("RGB", (64, 32), "white").save(image)
            with patch.dict(sys.modules, {"pytesseract": fake}), patch.object(
                ocr.TesseractTextRecognizer, "available", return_value=True
            ):
                result = ocr.TesseractTextRecognizer(Settings()).recognize(image)
        self.assertEqual(result.blocks, ())
        self.assertEqual(seen.get("timeout"), 30)


if __name__ == "__main__":
    unittest.main()
