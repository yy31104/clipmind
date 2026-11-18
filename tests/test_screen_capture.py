from pathlib import Path
import asyncio
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

from clipmind import media, visual_states
from clipmind.media import Frame


class IncrementalScreenTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required to verify decoder timestamps')
    def test_native_onset_uses_a_confirmed_source_frame_between_sample_times(self):
        async def check(work: Path):
            video = work / 'source.mp4'
            subprocess.run([
                'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi',
                '-i', 'color=black:s=640x360:r=30:d=3',
                '-vf', "drawbox=70:100:360:100:white:fill:enable='eq(n,42)+gte(n,44)'",
                '-c:v', 'libx264', '-threads', '2', str(video),
            ], check=True, capture_output=True)
            frames = await media.sample_frames(video, work / 'samples', fps=2, width=640)
            samples = [(frame.timestamp, frame.path) for frame in frames]
            kept = media.dedupe(frames)
            self.assertEqual([frame.timestamp for frame in kept], [0, 1.5])
            settled_path = kept[1].path
            blocked = work / 'blocked'
            blocked.write_bytes(b'existing file')
            await media.refine_native_onsets(video, samples, kept, blocked)
            self.assertEqual(kept[1].timestamp, 1.5)
            self.assertEqual(blocked.read_bytes(), b'existing file')
            await media.refine_native_onsets(video, samples, kept, work / 'native')
            self.assertEqual(kept[1].timestamp, 1.466666667)
            self.assertEqual(kept[1].source_timestamp, 1.5)
            self.assertEqual(kept[1].path, settled_path)

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required to verify stream selection')
    def test_native_onset_cannot_use_an_alternate_video_track(self):
        async def check(work: Path):
            video = work / 'two-tracks.mp4'
            subprocess.run([
                'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                "color=black:s=320x180:r=30:d=3,drawbox=35:50:180:50:white:fill:enable='gte(n,43)'",
                '-f', 'lavfi', '-i',
                "color=black:s=640x360:r=30:d=3,drawbox=70:100:360:100:white:fill:enable='gte(n,45)'",
                '-map', '0:v', '-map', '1:v', '-disposition:v:0', '0',
                '-disposition:v:1', 'default', '-c:v', 'libx264', '-threads', '2', str(video),
            ], check=True, capture_output=True)
            frames = await media.sample_frames(video, work / 'samples', fps=2, width=640)
            self.assertTrue(all(frame.source_stream_index == 1 for frame in frames))
            samples = [(frame.timestamp, frame.path) for frame in frames]
            kept = media.dedupe(frames)
            await media.refine_native_onsets(video, samples, kept, work / 'native')
            self.assertEqual([frame.timestamp for frame in kept], [0, 1.5])

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    def test_settled_image_keeps_interval_onset_and_its_own_capture_time(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = []
            for index, (timestamp, level) in enumerate(((0, 15), (0.5, 20), (1, 25), (3, 30))):
                path = Path(directory)/f'{index}.png'
                image = Image.new('RGB', (640, 360), (level, level, level))
                ImageDraw.Draw(image).text((30, 90), 'A stable information page', fill='white')
                image.save(path)
                frames.append(Frame(index, timestamp, path, source_timestamp=timestamp))
            kept = media.dedupe(frames)
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].timestamp, 0)
            self.assertEqual(kept[0].source_timestamp, 1)
            self.assertEqual(kept[0].path.name, '2.png')
            self.assertEqual(kept[0].stable_duration, 3)

    def test_repeated_characters_still_form_a_complete_build(self):
        frames = [Frame(0, 0, Path('a.jpg'), lines=('x = x + x',)),
                  Frame(1, 12, Path('b.jpg'), lines=('x = x + x', 'x = x + x'))]
        groups = visual_states.group_progressive_builds(frames)
        self.assertEqual(len(groups), 1)
        self.assertIs(groups[0].representative, frames[1])

    def test_replaced_values_are_not_a_monotonic_build(self):
        frames = [Frame(0, 0, Path('a.jpg'), lines=('print(1)',)),
                  Frame(1, 12, Path('b.jpg'), lines=('print(2)', 'print(2)'))]
        self.assertEqual(visual_states.group_progressive_builds(frames), [])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs local FFmpeg')
    def test_sample_image_and_capture_time_refer_to_the_same_source_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(10):
                Image.new('RGB', (64, 64), 'white' if index < 2 else 'red').save(root/f'{index:02d}.png')
            subprocess.run(['ffmpeg', '-v', 'error', '-framerate', '10', '-i', str(root/'%02d.png'),
                            '-c:v', 'ffv1', str(root/'input.mkv')], check=True)
            frames = asyncio.run(media.sample_frames(root/'input.mkv', root/'sampled', fps=2, width=64))
            self.assertEqual([frame.source_timestamp for frame in frames], [0.0, 0.5])
            with Image.open(frames[0].path) as image:
                self.assertGreater(min(image.getpixel((32, 32))), 240)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs local FFmpeg')
    def test_sampling_uses_fixed_time_buckets_without_accumulating_frame_rounding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=white:s=64x64:r=25:d=4',
                            '-c:v', 'ffv1', str(root/'input.mkv')], check=True)
            frames = asyncio.run(media.sample_frames(root/'input.mkv', root/'sampled', fps=2, width=64))
            self.assertEqual(len(frames), 8)
            self.assertEqual([frame.source_timestamp for frame in frames], [0, 0.52, 1, 1.52, 2, 2.52, 3, 3.52])

    def test_layout_and_new_code_are_distinguished_without_removing_frames(self):
        lines = ('let receiver = channel();', 'send(receiver, 1);')
        with tempfile.TemporaryDirectory() as directory:
            frames = []
            for index, x in enumerate((0.1, 0.25, 0.25)):
                text = lines if index < 2 else (*lines, 'send(receiver, 2);')
                path = Path(directory)/f'{index}.png'
                image = Image.new('RGB', (640, 360), '#171717')
                draw = ImageDraw.Draw(image)
                for i, line in enumerate(text):
                    draw.text((int(x*640), int((0.3+i*0.08)*360)), line, fill='white')
                image.save(path)
                frame = Frame(index, index, path, lines=text)
                frame.ocr_layout = tuple({'text': line, 'bbox': [x, 0.3 + i * 0.08, 0.5, 0.05]}
                                         for i, line in enumerate(frame.lines))
                frames.append(frame)
            visual_states.annotate_change_kinds(frames)
            self.assertEqual([frame.change_kind for frame in frames], ['content', 'layout', 'build'])
            self.assertEqual(len(frames), 3)

    def test_unreadable_or_image_only_change_stays_uncertain(self):
        frames = [Frame(0, 0, Path('a.jpg'), lines=('A page',)),
                  Frame(1, 1, Path('b.jpg'), ocr_warning='OCR failed'),
                  Frame(2, 2, Path('c.jpg'))]
        visual_states.annotate_change_kinds(frames)
        self.assertEqual([frame.change_kind for frame in frames[1:]], ['uncertain', 'uncertain'])

    def test_small_persistent_code_additions_survive_global_hash_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = []
            for index, lines in enumerate((1, 1, 2, 2, 3, 3)):
                path = Path(directory) / f'{index}.png'
                image = Image.new('RGB', (640, 360), '#171717')
                draw = ImageDraw.Draw(image)
                draw.text((35, 25), 'Sending a message', fill='white')
                for line in range(lines):
                    draw.text((35, 130 + line * 18), f'send_message(connection, {line}, data);', fill='white')
                image.save(path)
                frames.append(Frame(index, index / 2, path, source_timestamp=index / 2))
            self.assertLessEqual(media.hamming(media.dhash(frames[0].path), media.dhash(frames[-1].path)), 6)
            kept = media.dedupe(frames)
            self.assertEqual([frame.timestamp for frame in kept], [0, 1, 2])
            self.assertEqual(kept[-1].source_timestamp, 2)
            self.assertEqual(kept[-1].observed_sample_count, 2)
            # Reading representatives may compact a build; the saved states
            # and their information times still include each addition.
            for frame, count in zip(kept, (1, 2, 3)):
                frame.lines = tuple(f'line {i}' for i in range(count))
            group = visual_states.group_progressive_builds(kept)[0]
            self.assertIs(group.representative, kept[-1])
            self.assertEqual(len(kept), 3)

    def test_one_sample_pixel_flash_does_not_become_a_detail_build(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = []
            for index in range(4):
                image = Image.new('RGB', (640, 360), 'black')
                draw = ImageDraw.Draw(image)
                draw.text((30, 30), 'Persistent page', fill='white')
                if index == 1:
                    draw.text((30, 150), 'compression flash', fill='white')
                path = Path(directory) / f'{index}.png'
                image.save(path)
                frames.append(Frame(index, index / 2, path))
            self.assertEqual(len(media.dedupe(frames)), 1)

    def test_fade_uses_a_visible_onset_and_preserves_the_settled_capture(self):
        before = Image.new('RGB', (640, 360), '#181818')
        ImageDraw.Draw(before).text((35, 25), 'Sending a message', fill='white')
        settled = before.copy()
        draw = ImageDraw.Draw(settled)
        for line in range(5):
            draw.text((35, 130 + line * 18), f'send_message(connection, {line}, data);', fill='white')
        examples = (
            ('fade', (0, 0, 0, 0, .28, .53, 1, 1, 1, 1, 1), 2.5, 3.0),
            ('hard cut', (0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1), 3.0, 3.0),
            ('flash', (0, 0, 0, 0, .30, 0, 0, 1, 1, 1, 1), 3.5, 3.5),
        )
        for name, opacities, onset, capture in examples:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                frames = []
                for index, opacity in enumerate(opacities):
                    path = Path(directory) / f'{index}.png'
                    Image.blend(before, settled, opacity).save(path)
                    frames.append(Frame(index, index / 2, path, source_timestamp=index / 2))
                kept = media.dedupe(frames)
                self.assertEqual(len(kept), 2)
                self.assertEqual(kept[1].timestamp, onset)
                self.assertEqual(kept[1].source_timestamp, capture)
                self.assertLess(kept[0].source_timestamp, onset)
                self.assertEqual(kept[1].path, Path(directory) / f'{round(capture * 2)}.png')


if __name__ == '__main__':
    unittest.main()
