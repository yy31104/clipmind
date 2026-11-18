import unittest
import os
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
from clipmind.config import Settings


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
                    paths.append(prepared.path)
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
    def test_prepared_dark_image_keeps_faint_and_bright_colored_strokes(self):
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
                self.assertNotEqual(path, prepared.path)
                with Image.open(prepared.path) as image:
                    self.assertEqual(image.size, (200, 160))
                    self.assertLess(image.getpixel((30, 21)), 240)
                    self.assertGreater(image.getpixel((30, 21)), 80)
                    self.assertLess(image.getpixel((70, 21)), 80)
                    self.assertLess(image.getpixel((110, 21)), 80)

            self.assertFalse(prepared.path.exists())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)

    def test_derivative_is_removed_when_recognition_fails(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (100, 80), "white").save(path)
            original = path.read_bytes()

            with self.assertRaisesRegex(RuntimeError, "recognizer failed"):
                with ocr._recognition_image(path, portable=True) as prepared:
                    self.assertTrue(prepared.path.is_file())
                    raise RuntimeError("recognizer failed")

            self.assertFalse(prepared.path.exists())
            self.assertEqual(path.read_bytes(), original)

    def test_large_light_image_is_not_downsampled(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            Image.new("RGB", (2600, 100), "white").save(path)
            with ocr._recognition_image(path) as prepared:
                self.assertEqual(prepared.path, path)

    def test_large_dark_input_keeps_the_bounded_legacy_preparation(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.png"
            Image.new("RGB", (160, 100), "black").save(path)
            with patch.object(ocr, "LOCAL_CONTRAST_PIXEL_LIMIT", 10_000), patch.object(
                ocr, "_dark_code_image", side_effect=AssertionError("local contrast ran above its pixel limit")
            ):
                with ocr._recognition_image(path, portable=True) as prepared, Image.open(prepared.path) as image:
                    self.assertEqual(image.size, (320, 200))
                    self.assertEqual(prepared.page_mode, 11)

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
                self.assertEqual(prepared.path, path)
                with Image.open(prepared.path) as preserved:
                    self.assertEqual(preserved.getpixel((20, 10)), (90, 20, 20))



class LayoutOrderTests(unittest.TestCase):
    def test_vision_upscales_a_dark_pane_without_changing_its_colors(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.png"
            image = Image.new("RGB", (400, 300), (80, 80, 80))
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 25, 370, 275), fill="white")
            # Under a broader dark threshold the pane joins the surrounding
            # frame, making its bounding box too large to be a local panel.
            draw.rectangle((120, 25, 135, 135), fill=(80, 80, 80))
            draw.rectangle((120, 120, 345, 240), fill=(30, 30, 30))
            draw.rectangle((136, 141, 186, 154), fill=(120, 35, 40))
            image.save(path)
            source_bytes = path.read_bytes()
            title = ocr.OCRBlock("slide title", bbox=(0.25, 0.1, 0.4, 0.05))
            code = ocr.OCRBlock("visible only at higher resolution", bbox=(0.34, 0.47, 0.13, 0.05))
            original_line = ocr.OCRBlock("from original", bbox=(0.34, 0.65, 0.25, 0.05))

            def recognize(prepared, *, languages):
                self.assertEqual(languages, ("en-US",))
                if prepared == path:
                    return ocr.OCRResult((title, original_line))
                with Image.open(prepared) as derivative:
                    self.assertEqual(derivative.size, (800, 600))
                    self.assertEqual(derivative.mode, "RGB")
                    self.assertEqual(derivative.getpixel((600, 450)), (30, 30, 30))
                    self.assertEqual(derivative.getpixel((310, 290)), (120, 35, 40))
                return ocr.OCRResult((title, code))

            with patch.object(ocr, "_recognize_in_worker", side_effect=recognize) as worker:
                result = ocr.VisionTextRecognizer(Settings(ocr_languages=("en-US",))).recognize(path)

            self.assertEqual(result.blocks, (title, code, original_line))
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(worker.call_args_list[1].args[0], path)
            self.assertFalse(worker.call_args_list[0].args[0].exists())
            self.assertEqual(path.read_bytes(), source_bytes)

    def test_a_failed_vision_pass_keeps_the_other_pass(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.png"
            image = Image.new("RGB", (400, 300), "white")
            ImageDraw.Draw(image).rectangle((45, 90, 330, 235), fill=(35, 33, 40))
            image.save(path)
            recognizer = ocr.VisionTextRecognizer(Settings(ocr_languages=("en-US",)))
            expected = ocr.OCRResult((ocr.OCRBlock("useful line"),))
            failure = ocr.OCRError("Vision worker timed out")
            for responses in ([failure, expected], [expected, failure]):
                with self.subTest(responses=responses):
                    with patch.object(ocr, "_recognize_in_worker", side_effect=responses) as worker:
                        self.assertEqual(recognizer.recognize(path), expected)
                    self.assertEqual(worker.call_count, 2)
                    self.assertEqual(worker.call_args_list[1].args[0], path)
                    self.assertFalse(worker.call_args_list[0].args[0].exists())

            with patch.object(ocr, "_recognize_in_worker", side_effect=[failure, failure]) as worker:
                with self.assertRaises(ocr.OCRError):
                    recognizer.recognize(path)
            self.assertEqual(worker.call_count, 2)

    def test_vision_reads_an_ordinary_light_slide_once_at_native_size(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "light.png"
            Image.new("RGB", (400, 300), "white").save(path)
            expected = ocr.OCRResult((ocr.OCRBlock("slide title"),))
            with patch.object(ocr, "_recognize_in_worker", return_value=expected) as worker:
                result = ocr.VisionTextRecognizer(Settings(ocr_languages=("en-US",))).recognize(path)

        self.assertEqual(result, expected)
        worker.assert_called_once_with(path, languages=("en-US",))

    def test_trailing_comments_stay_on_their_code_lines(self):
        def block(text, x, y, width):
            return ocr.OCRBlock(text, bbox=(x, y, width, 0.03))

        code1 = block("int argc;", 0.05, 0.2, 0.2)
        code2 = block("char *argv[MAX];", 0.05, 0.25, 0.25)
        note1 = block("// number of args", 0.5, 0.2, 0.3)
        note2 = block("// argument list", 0.5, 0.25, 0.3)
        ordered = ocr._reading_order([note2, code2, note1, code1])
        self.assertEqual([item.text for item in ordered],
                         ["int argc;", "// number of args", "char *argv[MAX];", "// argument list"])

    def test_portable_preparation_enhances_dark_code_inside_a_light_slide(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "slide.png"
            image = Image.new("RGB", (400, 300), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((50, 100, 350, 250), fill=(30, 30, 30))
            draw.text((70, 150), "requires", fill=(150, 90, 200))
            image.save(path)
            with ocr._recognition_image(path, portable=True) as prepared, Image.open(prepared.path) as result:
                pixels = result.convert("L").resize((400, 300))
                self.assertGreater(pixels.getpixel((300, 230)), 200)
                self.assertEqual(pixels.getpixel((20, 20)), 255)

    def test_portable_dark_terminal_preserves_intermediate_stroke_levels(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terminal.png"
            image = Image.new("RGB", (100, 80), (14, 14, 14))
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 20, 29, 39), fill=(64, 8, 8))
            draw.rectangle((40, 20, 59, 39), fill=(8, 175, 8))
            image.save(path)
            with ocr._recognition_image(path, portable=True) as prepared, Image.open(prepared.path) as result:
                frame = result.resize((100, 80))
                levels = [frame.getpixel((x, 30)) for x in (76, 20, 50)]
                self.assertGreater(levels[0], levels[1])
                self.assertGreater(levels[1], levels[2])
            self.assertFalse(prepared.path.exists())

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

            sizes = []

            def recognize(prepared, *, languages):
                seen.append(prepared)
                self.assertEqual(languages, ("en-US",))
                with Image.open(prepared) as image:
                    sizes.append(image.size)
                return expected

            with patch.object(ocr, "_recognize_in_worker", side_effect=recognize):
                result = ocr.VisionTextRecognizer(
                    SimpleNamespace(ocr_languages=("en-US",))
                ).recognize(path)

            # The prepared derivative is read first; the original only adds
            # lines the prepared pass missed, so the same block is not doubled.
            self.assertEqual(sizes, [(200, 160), (100, 80)])
            self.assertEqual(result, expected)
            self.assertFalse(seen[0].exists())
            self.assertEqual(seen[1], path)

    def test_vision_recovers_a_line_the_prepared_pass_missed_only_where_ink_exists(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            image = Image.new("RGB", (200, 100), "black")
            ImageDraw.Draw(image).rectangle((20, 60, 120, 70), fill="white")
            image.save(path)
            first = ocr.OCRBlock("top line", 1.0, (0.1, 0.1, 0.5, 0.1))
            missed = ocr.OCRBlock("lower line", 1.0, (0.1, 0.6, 0.5, 0.1))
            faint = ocr.OCRBlock("background", 0.5, (0.7, 0.3, 0.2, 0.1))
            replies = iter([ocr.OCRResult((first,)), ocr.OCRResult((first, missed, faint))])
            with patch.object(ocr, "_recognize_in_worker", side_effect=lambda *a, **k: next(replies)):
                result = ocr.VisionTextRecognizer(SimpleNamespace(ocr_languages=("en-US",))).recognize(path)
            self.assertEqual([block.text for block in result.blocks], ["top line", "lower line"])

    def test_vision_keeps_dark_letters_on_a_light_card_inside_a_dark_screen(self):
        from PIL import Image, ImageDraw, ImageFont

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "editor.png"
            image = Image.new("RGB", (640, 360), (24, 29, 40))
            draw = ImageDraw.Draw(image)
            draw.rectangle((230, 130, 435, 210), fill=(229, 229, 229))
            font = ImageFont.load_default(size=14)
            title = "Build target: RELEASE"
            draw.text((252, 159), title, font=font, fill=(130, 130, 130))
            left, top, right, bottom = draw.textbbox((252, 159), title, font=font)
            image.save(path)
            block = ocr.OCRBlock(title, 0.96, (left / 640, top / 360,
                                               (right - left) / 640, (bottom - top) / 360))
            with patch.object(ocr, "_recognize_in_worker",
                              side_effect=[ocr.OCRResult(()), ocr.OCRResult((block,))]):
                result = ocr.VisionTextRecognizer(Settings(ocr_languages=("en-US",))).recognize(path)
            self.assertEqual(result.blocks, (block,))

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

    def test_tesseract_uses_block_mode_only_for_a_nearly_full_dark_text_page(self):
        from PIL import Image, ImageDraw

        for layout, expected in (("single", "--psm 6"), ("mixed", "--psm 11"), ("split", "--psm 11")):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "code.png"
                image = Image.new("RGB", (640, 360), (15, 25, 52))
                draw = ImageDraw.Draw(image)
                if layout == "mixed":
                    draw.rectangle((0, 0, 639, 75), fill="white")
                pane_columns = (range(50, 240, 12), range(394, 602, 12)) if layout == "split" else (range(50, 420, 12),)
                for line_y in range(90, 300, 30):
                    for columns in pane_columns:
                        for char_x in columns:
                            draw.rectangle((char_x, line_y, char_x + 7, line_y + 9), fill=(140, 95, 125))
                image.save(path)
                called = []

                def image_to_data(source, **kwargs):
                    called.append(kwargs["config"])
                    self.assertEqual(source.width, 2048)
                    return {"text": []}

                fake = SimpleNamespace(image_to_data=image_to_data, Output=SimpleNamespace(DICT="dict"))
                with patch.dict("sys.modules", {"pytesseract": fake}), patch.object(
                    ocr.TesseractTextRecognizer, "available", return_value=True
                ):
                    ocr.TesseractTextRecognizer(
                        SimpleNamespace(tesseract_languages="eng")
                    ).recognize(path)
                self.assertEqual(called, [expected])

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


class TesseractLanguageConfigurationTests(unittest.TestCase):
    def test_default_prefers_english_and_keeps_both_chinese_models(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings().tesseract_languages, "eng+chi_sim+chi_tra")
            self.assertEqual(Settings.from_env().tesseract_languages, "eng+chi_sim+chi_tra")

    def test_explicit_language_order_is_preserved(self):
        with patch.dict(os.environ, {"CLIPMIND_TESSERACT_LANGUAGES": "chi_tra+eng"}):
            self.assertEqual(Settings.from_env().tesseract_languages, "chi_tra+eng")


if __name__ == "__main__":
    unittest.main()
