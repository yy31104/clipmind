from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clipmind import visual_states
from clipmind.media import Frame


class CanonicalVisualStateTests(unittest.TestCase):
    def test_retain_all_is_untruncated_chronological_and_collision_safe(self) -> None:
        timestamps = [5.9, 0.2, 0.8, 1.1, 1.7, 2.0, 2.4, 3.2, 3.7, 4.1, 4.9, 5.1]

        with tempfile.TemporaryDirectory() as tempdir:
            workdir = Path(tempdir)
            samples = workdir / "samples"
            samples.mkdir()
            frames: list[Frame] = []
            original_paths: list[Path] = []
            for index, timestamp in enumerate(timestamps):
                path = samples / f"sample-{index}.jpg"
                path.write_bytes(f"state-{index}".encode())
                original_paths.append(path)
                frames.append(Frame(index=index, timestamp=timestamp, path=path))

            retained = visual_states.retain_all(
                frames,
                workdir / "visual_states" / "all",
            )

            self.assertEqual(len(retained), 12)
            self.assertEqual(
                [frame.timestamp for frame in retained],
                sorted(timestamps),
            )
            self.assertEqual(len({frame.path for frame in retained}), 12)
            self.assertTrue(
                all(frame.path.parent == workdir / "visual_states" / "all" for frame in retained)
            )
            self.assertEqual(
                {frame.path.read_bytes() for frame in retained},
                {f"state-{index}".encode() for index in range(12)},
            )
            self.assertTrue(all(not path.exists() for path in original_paths))


if __name__ == "__main__":
    unittest.main()
