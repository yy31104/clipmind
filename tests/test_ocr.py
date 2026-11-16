import unittest
import tempfile
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import sys
from contextlib import nullcontext, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import ocr


def _controlled_vision_command():
    code = """import json,os,pathlib,sys,time
count=0
for line in sys.stdin:
    message=json.loads(line)
    if pathlib.Path(message['image']).name=='hang.png':
        print('controlled native stall',file=sys.stderr,flush=True)
        time.sleep(60)
    count+=1
    print(json.dumps({'status':'ok','blocks':[{'text':f'{os.getpid()}:{count}','bbox':[.1,.2,.3,.4]}]}),flush=True)
"""
    return [sys.executable, "-u", "-c", code]


def _sleeping_vision_command():
    return [sys.executable, "-u", "-c", "import time;time.sleep(60)"]


class _Handler:
    def __init__(self, seen):
        self.seen = seen

    def alloc(self):
        return self

    def initWithURL_options_(self, url, options):
        self.seen.append((url, options))
        return self

    def performRequests_error_(self, requests, error):
        return True, None


class _Request:
    def alloc(self):
        return self

    def init(self):
        return self

    def setRecognitionLanguages_(self, languages):
        pass

    def setRecognitionLevel_(self, level):
        pass

    def setUsesLanguageCorrection_(self, enabled):
        pass

    def results(self):
        return []


class VisionCompatibilityTests(unittest.TestCase):
    def test_image_request_uses_no_options_dictionary(self):
        seen = []
        vision = SimpleNamespace(
            VNImageRequestHandler=_Handler(seen),
            VNRecognizeTextRequest=_Request(),
        )
        foundation_url = SimpleNamespace(fileURLWithPath_=lambda path: path)
        objc = SimpleNamespace(autorelease_pool=lambda: nullcontext())

        with patch.multiple(
            ocr,
            _IMPORT_ERROR=None,
            Vision=vision,
            NSURL=foundation_url,
            objc=objc,
        ):
            result = ocr.recognize(Path("fixture.png"))

        self.assertEqual(result.blocks, ())
        self.assertEqual(seen, [("fixture.png", None)])

    def test_native_requests_are_serial_even_with_two_ocr_workers(self):
        active = maximum = 0
        guard = threading.Lock()

        class Handler(_Handler):
            def performRequests_error_(self, requests, error):
                nonlocal active, maximum
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.02)
                with guard:
                    active -= 1
                return True, None

        vision = SimpleNamespace(VNImageRequestHandler=Handler([]),
                                 VNRecognizeTextRequest=_Request())
        with patch.multiple(
            ocr, _IMPORT_ERROR=None, Vision=vision,
            NSURL=SimpleNamespace(fileURLWithPath_=str),
            objc=SimpleNamespace(autorelease_pool=lambda: nullcontext()),
        ):
            with ThreadPoolExecutor(max_workers=2) as workers:
                list(workers.map(ocr.recognize, [Path("a.png"), Path("b.png")]))
        self.assertEqual(maximum, 1)


class VisionWorkerTests(unittest.TestCase):
    def setUp(self):
        ocr._close_vision_worker()

    def tearDown(self):
        ocr._close_vision_worker()

    def test_real_child_is_reused_then_recycled(self):
        with patch.object(ocr, "_vision_worker_command", _controlled_vision_command), patch.object(
            ocr, "_VISION_MAX_REQUESTS", 2
        ):
            first = ocr._recognize_in_worker(Path("first.png"), languages=("en-US",))
            first_process, first_reader = ocr._worker_process, ocr._worker_reader
            second = ocr._recognize_in_worker(Path("second.png"), languages=("en-US",))
            third = ocr._recognize_in_worker(Path("third.png"), languages=("en-US",))
        first_pid, first_count = first.lines[0].split(":")
        second_pid, second_count = second.lines[0].split(":")
        third_pid, third_count = third.lines[0].split(":")
        self.assertEqual(first_pid, second_pid)
        self.assertEqual((first_count, second_count), ("1", "2"))
        self.assertNotEqual(first_pid, third_pid)
        self.assertEqual(third_count, "1")
        self.assertIsNotNone(first_process.poll())
        self.assertFalse(first_reader.is_alive())

    def test_timeout_reaps_child_and_next_request_starts_fresh(self):
        with patch.object(ocr, "_vision_worker_command", _controlled_vision_command):
            first = ocr._recognize_in_worker(Path("first.png"), languages=("en-US",))
            first_pid = first.lines[0].split(":")[0]
            process = ocr._worker_process
            readers = (ocr._worker_reader, ocr._worker_error_reader)
            with patch.object(ocr, "_VISION_REQUEST_TIMEOUT", 0.1):
                started = time.monotonic()
                with self.assertRaisesRegex(ocr.OCRError, "deadline") as caught:
                    ocr._recognize_in_worker(Path("hang.png"), languages=("en-US",))
            self.assertLess(time.monotonic() - started, 3)
            self.assertIsNone(ocr._worker_process)
            self.assertIsNone(ocr._worker_connection)
            self.assertIsNotNone(process.poll())
            self.assertTrue(all(not reader.is_alive() for reader in readers))
            self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)
            self.assertIn("controlled native stall", "".join(caught.exception.__notes__))
            following = ocr._recognize_in_worker(Path("next.png"), languages=("en-US",))
            self.assertNotEqual(first_pid, following.lines[0].split(":")[0])

    def test_idle_child_is_reaped(self):
        with patch.object(ocr, "_vision_worker_command", _controlled_vision_command), patch.object(
            ocr, "_VISION_IDLE_SECONDS", 0.05
        ):
            ocr._recognize_in_worker(Path("first.png"), languages=("en-US",))
            process = ocr._worker_process
            deadline = time.monotonic() + 3
            while (ocr._worker_process is not None or process.poll() is None) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(ocr._worker_process)
            self.assertIsNotNone(process.poll())

    def test_timeout_removes_parent_owned_derivative(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "screen.png"
            Image.new("RGB", (100, 80), "black").save(image)
            original = image.read_bytes()
            paths = []
            prepare = ocr._recognition_image

            @contextmanager
            def recorded(path):
                with prepare(path) as prepared:
                    paths.append(prepared)
                    yield prepared

            with patch.object(ocr, "_vision_worker_command", _sleeping_vision_command), patch.object(
                ocr, "_VISION_REQUEST_TIMEOUT", 0.5
            ), patch.object(ocr, "_recognition_image", recorded):
                with self.assertRaisesRegex(ocr.OCRError, "deadline"):
                    ocr.VisionTextRecognizer(
                        SimpleNamespace(ocr_languages=("en-US",))
                    ).recognize(image)
            self.assertEqual(image.read_bytes(), original)
            self.assertTrue(paths)
            self.assertTrue(all(not path.exists() for path in paths))
            self.assertIsNone(ocr._worker_process)


class RecognitionImageTests(unittest.TestCase):
    def test_faint_layer_is_removed_without_losing_bright_colored_text(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            source = Image.new("RGB", (100, 80), (12, 12, 12))
            draw = ImageDraw.Draw(source)
            draw.rectangle((10, 10, 20, 11), fill=(45, 45, 45))
            draw.rectangle((30, 10, 40, 11), fill=(220, 0, 0))
            draw.rectangle((50, 10, 60, 11), fill=(0, 220, 0))
            source.save(path)
            original = hashlib.sha256(path.read_bytes()).hexdigest()

            with ocr._recognition_image(path) as prepared:
                self.assertNotEqual(path, prepared)
                with Image.open(prepared) as image:
                    self.assertEqual(image.size, (200, 160))
                    self.assertEqual(image.getpixel((30, 21)), 255)
                    self.assertLess(image.getpixel((70, 21)), 80)
                    self.assertLess(image.getpixel((110, 21)), 80)

            self.assertFalse(prepared.exists())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)

    def test_derivative_is_removed_when_recognition_fails(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (100, 80), "white").save(path)
            original = path.read_bytes()

            with self.assertRaisesRegex(RuntimeError, "recognizer failed"):
                with ocr._recognition_image(path, portable=True) as prepared:
                    self.assertTrue(prepared.is_file())
                    raise RuntimeError("recognizer failed")

            self.assertFalse(prepared.exists())
            self.assertEqual(path.read_bytes(), original)

    def test_large_light_image_is_not_downsampled(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (2600, 100), "white").save(path)
            with ocr._recognition_image(path) as prepared:
                self.assertEqual(prepared, path)

    def test_mixed_light_panel_keeps_dim_colored_syntax_intact(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            image = Image.new("RGB", (100, 80), (12, 12, 12))
            draw = ImageDraw.Draw(image)
            draw.rectangle((50, 5, 95, 30), fill="white")
            draw.line((10, 10, 30, 10), fill=(90, 20, 20), width=2)
            image.save(path)
            with ocr._recognition_image(path) as prepared:
                self.assertEqual(prepared, path)
                with Image.open(prepared) as preserved:
                    self.assertEqual(preserved.getpixel((20, 10)), (90, 20, 20))



class LayoutOrderTests(unittest.TestCase):
    def test_fragments_on_the_same_line_use_horizontal_order(self):
        left = ocr.OCRBlock("let value", bbox=(0.05, 0.21, 0.12, 0.04))
        right = ocr.OCRBlock("function()", bbox=(0.19, 0.2, 0.5, 0.06))
        self.assertEqual(ocr._reading_order([right, left]), (left, right))

    def test_header_then_complete_columns_instead_of_interleaved_rows(self):
        def block(text, x, y, width, height=0.02):
            return ocr.OCRBlock(text, bbox=(x, y, width, height))

        title = block("header", 0.05, 0.05, 0.8, 0.05)
        left1 = block("left first", 0.05, 0.2, 0.25)
        left2 = block("left second", 0.05, 0.25, 0.25)
        right1 = block("right first", 0.6, 0.2, 0.25)
        right2 = block("right second", 0.6, 0.25, 0.25)
        unknown = ocr.OCRBlock("unpositioned")
        ordered = ocr._reading_order([right2, left1, unknown, right1, title, left2])
        self.assertEqual([item.text for item in ordered],
                         ["header", "left first", "left second", "right first",
                          "right second", "unpositioned"])

    def test_vision_keeps_normalized_boxes_after_preprocessing(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (100, 80), "black").save(path)
            expected = ocr.OCRResult((ocr.OCRBlock("code", bbox=(0.1, 0.2, 0.4, 0.1)),))
            seen = []

            def recognize(prepared, *, languages):
                seen.append(prepared)
                self.assertEqual(languages, ("en-US",))
                with Image.open(prepared) as image:
                    self.assertEqual(image.size, (200, 160))
                return expected

            with patch.object(ocr, "_recognize_in_worker", side_effect=recognize):
                result = ocr.VisionTextRecognizer(
                    SimpleNamespace(ocr_languages=("en-US",))
                ).recognize(path)

            self.assertEqual(result, expected)
            self.assertFalse(seen[0].exists())

    def test_tesseract_boxes_use_prepared_dimensions(self):
        from PIL import Image

        def image_to_data(source, **kwargs):
            self.assertEqual(source.size, (200, 160))
            return {"text": ["read", "me"], "block_num": [1, 1], "par_num": [1, 1],
                    "line_num": [1, 1], "left": [50, 110], "top": [40, 40],
                    "width": [50, 20], "height": [20, 20], "conf": [90, 90]}

        fake = SimpleNamespace(image_to_data=image_to_data,
                               Output=SimpleNamespace(DICT="dict"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (100, 80), "white").save(path)
            with patch.dict("sys.modules", {"pytesseract": fake}), patch.object(
                ocr.TesseractTextRecognizer, "available", return_value=True
            ):
                result = ocr.TesseractTextRecognizer(
                    SimpleNamespace(tesseract_languages="eng")
                ).recognize(path)
        self.assertEqual(result.blocks[0].bbox, (0.25, 0.25, 0.4, 0.125))

    def test_tesseract_does_not_join_distant_columns_into_one_box(self):
        from PIL import Image

        data = {"text": ["right", "left"], "block_num": [1, 1], "par_num": [1, 1],
                "line_num": [1, 1], "left": [150, 10], "top": [40, 40],
                "width": [30, 30], "height": [10, 10], "conf": [90, 90]}
        fake = SimpleNamespace(image_to_data=lambda *args, **kwargs: data,
                               Output=SimpleNamespace(DICT="dict"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (100, 80), "white").save(path)
            with patch.dict("sys.modules", {"pytesseract": fake}), patch.object(
                ocr.TesseractTextRecognizer, "available", return_value=True
            ):
                result = ocr.TesseractTextRecognizer(
                    SimpleNamespace(tesseract_languages="eng")
                ).recognize(path)
        self.assertEqual(result.lines, ["left", "right"])
        self.assertEqual(result.blocks[0].bbox, (0.05, 0.25, 0.15, 0.0625))


if __name__ == "__main__":
    unittest.main()
