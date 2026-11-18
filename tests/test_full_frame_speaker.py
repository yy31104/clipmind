from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from clipmind import screen_motion, visual_states
from clipmind.media import Frame


class FullFrameSpeakerTests(unittest.TestCase):
    def setUp(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV is unavailable")
        storage = TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.root = Path(storage.name)

    def room_frames(self, add_detail=None):
        frames, faces = [], {}
        for position in range(4):
            portrait_x = 525 + 44 * position
            path = self.root / f"room-{position}.png"
            image = Image.new("RGB", (1280, 720), (142, 139, 141))
            draw = ImageDraw.Draw(image)
            draw.polygon(((445, 720), (455, 450), (505, 365), (740, 350),
                          (820, 425), (880, 720)), fill=(47, 69, 85))
            draw.ellipse((portrait_x, 58, portrait_x + 185, 317), fill=(113, 66, 47))
            draw.rectangle((portrait_x + 53, 288, portrait_x + 95, 376), fill=(113, 66, 47))
            draw.ellipse((portrait_x + 51, 170, portrait_x + 75, 190), fill=(32, 25, 24))
            draw.ellipse((579 + 15 * position, 460, 656 + 15 * position, 594), fill=(113, 66, 47))
            if add_detail and position >= 2:
                add_detail(draw)
            image.save(path)
            faces[path] = ((portrait_x / 1280, 62 / 720, 180 / 1280, 248 / 720),)
            frames.append(Frame(5 + 4 * position, float(position), path,
                                source_timestamp=position + 0.12, observed_sample_count=2,
                                stable_duration=0.5, change_kind="uncertain",
                                change_reason=visual_states.NO_READABLE_TEXT_REASON))
        return frames, faces

    def post_pass(self, frames, faces):
        original = [replace(frame) for frame in frames]
        visual_states._annotate_full_frame_speakers(frames, face_boxes=faces.get)
        for frame, old in zip(frames, original, strict=True):
            self.assertEqual(replace(frame, change_kind=old.change_kind, change_reason=old.change_reason), old)
        return [frame.index for frame, old in zip(frames, original, strict=True)
                if frame.change_kind != old.change_kind]

    def test_repeated_camera_motion_with_local_face_support(self):
        frames, faces = self.room_frames()
        self.assertTrue(self.post_pass(frames, faces))
        self.assertEqual(frames[0].change_kind, "uncertain")
        self.assertEqual(frames[2].change_kind, "speaker")
        self.assertEqual(frames[2].change_reason, screen_motion.FULL_FRAME_SPEAKER_REASON)

    def test_projection_and_other_classifications_are_complete_before_the_room_pass(self):
        frames, faces = self.room_frames()
        for frame in frames:
            frame.change_kind, frame.change_reason = "uncertain", ""
        with patch.object(screen_motion.screen_faces, "boxes", side_effect=faces.get):
            previous = [replace(frame) for frame in frames]
            with patch.object(visual_states, "_annotate_full_frame_speakers"):
                visual_states.annotate_change_kinds(previous)
            visual_states.annotate_change_kinds(frames)
        differences = []
        for old, current in zip(previous, frames, strict=True):
            if current.change_kind != old.change_kind:
                differences.append(current.index)
                self.assertEqual(old.change_reason, visual_states.NO_READABLE_TEXT_REASON)
                self.assertEqual(current.change_kind, "speaker")
            self.assertEqual(replace(current, change_kind=old.change_kind, change_reason=old.change_reason), old)
        self.assertTrue(differences)

    def test_persistent_textless_diagram_is_preserved(self):
        def diagram(draw):
            draw.line((920, 300, 992, 350, 1050, 300), fill="#f4ec25", width=6)
            for x, y in ((920, 300), (992, 350), (1050, 300)):
                draw.ellipse((x - 17, y - 17, x + 17, y + 17), fill="#2159e0")

        frames, faces = self.room_frames(diagram)
        self.assertNotIn(frames[2].index, self.post_pass(frames, faces))

    def test_native_five_pixel_stroke_is_preserved(self):
        # Neutral foreground RGB is already in the room palette. This
        # requires the native out-of-silhouette check.
        frames, faces = self.room_frames(lambda draw: draw.line((1043, 300, 1047, 300), fill=(47, 69, 85)))
        self.assertNotIn(frames[2].index, self.post_pass(frames, faces))

    def test_new_prop_inside_the_silhouette_can_be_preserved_by_color(self):
        def prop(draw):
            draw.rectangle((752, 579, 769, 681), fill="#f0eb10")
            draw.ellipse((752, 574, 769, 586), fill="#f0eb10")

        frames, faces = self.room_frames(prop)
        self.assertNotIn(frames[2].index, self.post_pass(frames, faces))

    def test_inner_diagram_reusing_existing_room_colors_is_preserved(self):
        wall = (142, 139, 141)
        cuff = (87, 110, 110)
        for color in (wall, cuff):
            with self.subTest(color=color):
                frames, faces = self.room_frames()
                for position, frame in enumerate(frames):
                    with Image.open(frame.path) as source:
                        image = source.copy()
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((495, 651, 577, 699), fill=cuff)
                    if position >= 2:
                        draw.line((672, 555, 719, 555, 719, 610, 752, 610), fill=color, width=7)
                        draw.ellipse((658, 541, 685, 569), fill=color)
                        draw.polygon(((741, 596), (767, 610), (741, 624)), fill=color)
                    image.save(frame.path)
                with patch.object(screen_motion.screen_faces, "boxes", side_effect=faces.get):
                    visual_states.annotate_change_kinds(frames)
                self.assertEqual(frames[2].change_kind, "uncertain")
                self.assertEqual(frames[2].change_reason, visual_states.NO_READABLE_TEXT_REASON)

    def test_foreground_occluder_leaves_the_boundary_unresolved(self):
        frames, faces = self.room_frames(
            lambda draw: draw.ellipse((22, 603, 246, 770), fill=(47, 69, 85)))
        self.assertNotIn(frames[2].index, self.post_pass(frames, faces))

    def test_no_face_and_one_face_are_both_insufficient(self):
        frames, faces = self.room_frames()
        for witnesses in ({}, dict(list(faces.items())[:1])):
            with self.subTest(witnesses=len(witnesses)):
                self.assertFalse(self.post_pass(frames, {frame.path:witnesses.get(frame.path, ()) for frame in frames}))

    def test_isolated_noise_does_not_equal_a_native_stroke(self):
        frames, faces = self.room_frames(lambda draw: draw.point([(1003, 340), (1022, 337)], fill="#ffffff"))
        self.assertIn(frames[2].index, self.post_pass(frames, faces))

    def test_warnings_and_richer_or_uncertain_reasons_stay_intact(self):
        frames, faces = self.room_frames()
        frames[1].ocr_warning = "forced OCR warning"
        frames[2].dedupe_warning = "forced comparison warning"
        frames[3].change_reason = "The different graphic has no text."
        self.assertFalse(self.post_pass(frames, faces))
