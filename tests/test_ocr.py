import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import ocr


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


if __name__ == "__main__":
    unittest.main()
