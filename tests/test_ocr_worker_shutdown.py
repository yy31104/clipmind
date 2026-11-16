import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from clipmind import ocr


@unittest.skipUnless(os.name == "posix", "Vision runs on macOS; inherited-pipe groups need POSIX")
class VisionWorkerShutdownTests(unittest.TestCase):
    def setUp(self):
        ocr._close_vision_worker()

    def tearDown(self):
        ocr._close_vision_worker()

    @staticmethod
    def command(marker, *, exit_after_reply):
        descendant = """import os,pathlib,signal,sys,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(3)
"""
        parent = """import json,pathlib,subprocess,sys,time
subprocess.Popen([sys.executable,'-u','-c',sys.argv[1],sys.argv[2]],stdin=subprocess.DEVNULL)
while not pathlib.Path(sys.argv[2]).exists():time.sleep(.005)
sys.stdin.readline()
if sys.argv[3]=='reply':
    print(json.dumps({'status':'ok','blocks':[]}),flush=True)
else:
    time.sleep(60)
"""
        return [sys.executable, "-u", "-c", parent, descendant, str(marker),
                "reply" if exit_after_reply else "hang"]

    def test_timeout_stops_inherited_pipe_writers_before_closing_readers(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            ocr, "_vision_worker_command",
            return_value=self.command(Path(folder) / "helper.pid", exit_after_reply=False),
        ), patch.object(ocr, "_VISION_REQUEST_TIMEOUT", .5):
            started = time.monotonic()
            with self.assertRaisesRegex(ocr.OCRError, "deadline"):
                ocr._recognize_in_worker(Path("synthetic.png"), languages=("en-US",))
            self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(ocr._worker_process)
        self.assertIsNone(ocr._worker_reader)
        self.assertIsNone(ocr._worker_error_reader)

    def test_exited_interpreter_does_not_leave_its_pipe_inheriting_helper(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            ocr, "_vision_worker_command",
            return_value=self.command(Path(folder) / "helper.pid", exit_after_reply=True),
        ):
            result = ocr._recognize_in_worker(Path("synthetic.png"), languages=("en-US",))
            self.assertEqual(result.blocks, ())
            process = ocr._worker_process
            readers = (ocr._worker_reader, ocr._worker_error_reader)
            process.wait(timeout=2)
            started = time.monotonic()
            ocr._close_vision_worker()
            self.assertLess(time.monotonic() - started, 2)
            self.assertTrue(all(not reader.is_alive() for reader in readers))
            self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)


if __name__ == "__main__":
    unittest.main()
