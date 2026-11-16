import unittest
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import ocr


def _controlled_vision_worker(connection):
    count = 0
    try:
        while True:
            image, languages = connection.recv()
            if Path(image).name == "hang.png":
                time.sleep(60)
            count += 1
            connection.send(("ok", ocr.OCRResult((ocr.OCRBlock(
                f"{os.getpid()}:{count}", bbox=(0.1, 0.2, 0.3, 0.4)
            ),))))
    except EOFError:
        pass
    finally:
        connection.close()


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
        with patch.object(ocr, "_vision_worker_loop", _controlled_vision_worker), patch.object(
            ocr, "_VISION_MAX_REQUESTS", 2
        ):
            first = ocr._recognize_in_worker(Path("first.png"), languages=("en-US",))
            second = ocr._recognize_in_worker(Path("second.png"), languages=("en-US",))
            third = ocr._recognize_in_worker(Path("third.png"), languages=("en-US",))
        first_pid, first_count = first.lines[0].split(":")
        second_pid, second_count = second.lines[0].split(":")
        third_pid, third_count = third.lines[0].split(":")
        self.assertEqual(first_pid, second_pid)
        self.assertEqual((first_count, second_count), ("1", "2"))
        self.assertNotEqual(first_pid, third_pid)
        self.assertEqual(third_count, "1")

    def test_timeout_reaps_child_and_next_request_starts_fresh(self):
        with patch.object(ocr, "_vision_worker_loop", _controlled_vision_worker):
            first = ocr._recognize_in_worker(Path("first.png"), languages=("en-US",))
            first_pid = first.lines[0].split(":")[0]
            with patch.object(ocr, "_VISION_REQUEST_TIMEOUT", 0.1):
                started = time.monotonic()
                with self.assertRaisesRegex(ocr.OCRError, "deadline"):
                    ocr._recognize_in_worker(Path("hang.png"), languages=("en-US",))
            self.assertLess(time.monotonic() - started, 3)
            self.assertIsNone(ocr._worker_process)
            self.assertIsNone(ocr._worker_connection)
            following = ocr._recognize_in_worker(Path("next.png"), languages=("en-US",))
            self.assertNotEqual(first_pid, following.lines[0].split(":")[0])

    def test_idle_child_is_reaped(self):
        with patch.object(ocr, "_vision_worker_loop", _controlled_vision_worker), patch.object(
            ocr, "_VISION_IDLE_SECONDS", 0.05
        ):
            ocr._recognize_in_worker(Path("first.png"), languages=("en-US",))
            deadline = time.monotonic() + 3
            while ocr._worker_process is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(ocr._worker_process)


if __name__ == "__main__":
    unittest.main()
