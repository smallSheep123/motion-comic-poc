# -*- coding: utf-8 -*-
import os
import json
import tempfile
import unittest
import wave

from PIL import Image

from motion_comic.alignment import (
    apply_page_alignment,
    apply_page_timeline,
    validate_page_timeline,
    validate_segments,
)
from motion_comic.compiler import compile_script
from motion_comic.renderer import rooted_audio_tracks
from gui.timeline_editor import build_editor_data, save_alignment


class TestAlignment(unittest.TestCase):
    def setUp(self):
        self.director = {
            "aspect": "9:16",
            "crossfade_sec": 0.6,
            "shots": [
                {"id": "p0_a", "page": "pages/p0.png", "motion": "HOLD",
                 "transition_out": "CUT", "narration": {"text": "第一句。"}},
                {"id": "p0_b", "page": "pages/p0.png", "motion": "HOLD",
                 "transition_out": "CUT", "narration": {"text": "第二句。"}},
                {"id": "p1_a", "page": "pages/p1.png", "motion": "HOLD",
                 "transition_out": "CUT", "narration": {"text": "第三句。"}},
            ],
        }
        self.segments = [
            {"shot_id": "p0_a", "source_start": 0.4, "source_end": 2.4,
             "gap_after": 0.5, "transition_out": "CROSSFADE", "transition_duration": 0.4},
            {"shot_id": "p0_b", "source_start": 2.7, "source_end": 4.2,
             "gap_after": 0.2, "transition_out": "FADE_BLACK", "transition_duration": 0.3},
        ]

    def test_apply_is_non_destructive_and_encodes_timing(self):
        updated = apply_page_alignment(self.director, "p0.png", "audio/p0.wav",
                                       self.segments, audio_duration=5.0)
        self.assertNotIn("audio", self.director["shots"][0]["narration"])
        first = updated["shots"][0]
        self.assertEqual(first["narration"]["source_start"], 0.4)
        self.assertEqual(first["narration"]["source_duration"], 2.0)
        self.assertAlmostEqual(first["fixed_duration"], 2.9)
        self.assertEqual(first["transition_sec"], 0.4)

    def test_rejects_overlap_and_oversized_transition(self):
        overlapping = [dict(self.segments[0]), dict(self.segments[1], source_start=2.0)]
        with self.assertRaisesRegex(ValueError, "重叠"):
            validate_segments(overlapping, ["p0_a", "p0_b"], 5.0)
        oversized = [dict(self.segments[0], source_end=0.7, gap_after=0,
                          transition_duration=0.5), self.segments[1]]
        with self.assertRaisesRegex(ValueError, "转场长于"):
            validate_segments(oversized, ["p0_a", "p0_b"], 5.0)

    def test_compiler_trims_shared_page_audio_and_uses_per_shot_transition(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "pages"))
            os.makedirs(os.path.join(root, "audio"))
            for page in ("p0.png", "p1.png"):
                Image.new("RGB", (90, 160), "white").save(os.path.join(root, "pages", page))
            audio = os.path.join(root, "audio", "p0.wav")
            with wave.open(audio, "wb") as file:
                file.setnchannels(1); file.setsampwidth(2); file.setframerate(8000)
                file.writeframes(b"\0\0" * 8000 * 5)
            updated = apply_page_alignment(self.director, "p0.png", "audio/p0.wav",
                                           self.segments, audio_duration=5.0)
            updated["shots"] = updated["shots"][:2]
            timeline = compile_script(updated, root=root)
            self.assertEqual(timeline["shots"][0]["transition_duration"], 0.4)
            self.assertAlmostEqual(timeline["shots"][1]["global_start"], 2.5)
            self.assertEqual(timeline["audio_tracks"][0]["source_start"], 0.4)
            self.assertEqual(timeline["audio_tracks"][0]["source_duration"], 2.0)
            rooted = rooted_audio_tracks(timeline["audio_tracks"], root)
            self.assertEqual(rooted[0]["source_start"], 0.4)
            self.assertEqual(rooted[0]["source_duration"], 2.0)
            self.assertTrue(os.path.isabs(rooted[0]["file"]))

    def test_editor_save_persists_alignment_without_rewriting_audio(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "pages"))
            os.makedirs(os.path.join(root, "audio"))
            Image.new("RGB", (90, 160), "white").save(os.path.join(root, "pages", "p0.png"))
            audio = os.path.join(root, "audio", "p0.wav")
            with wave.open(audio, "wb") as file:
                file.setnchannels(1); file.setsampwidth(2); file.setframerate(8000)
                file.writeframes(b"\0\0" * 8000 * 5)
            one_page = dict(self.director, shots=self.director["shots"][:2])
            with open(os.path.join(root, "director.json"), "w", encoding="utf-8") as file:
                json.dump(one_page, file)
            before_size = os.path.getsize(audio)
            data = build_editor_data(root)
            self.assertEqual(data["pages"][0]["source"], {"kind": "file", "path": "audio/p0.wav"})
            result = save_alignment(root, {
                "page": "p0.png", "source": {"kind": "file", "path": "audio/p0.wav"},
                "audio_duration": 5.0,
                "audio_clips": [
                    {"id": "a1", "source_start": 0.4, "source_end": 2.4,
                     "timeline_start": 0.0, "text": "第一句。"},
                    {"id": "a2", "source_start": 2.4, "source_end": 4.2,
                     "timeline_start": 2.5, "text": "第二句。"},
                    {"id": "a3", "source_start": 4.2, "source_end": 5.0,
                     "timeline_start": 4.3, "text": "补充分段。"},
                ],
                "visual_clips": [
                    {"shot_id": "p0_a", "timeline_start": 0.0, "timeline_end": 3.0},
                    {"shot_id": "p0_b", "timeline_start": 3.0, "timeline_end": 5.1},
                ],
            })
            self.assertTrue(result["ok"])
            self.assertEqual(os.path.getsize(audio), before_size)
            with open(os.path.join(root, "timeline_alignment.json"), encoding="utf-8") as file:
                saved = json.load(file)
            page = saved["pages"]["p0.png"]
            self.assertEqual(saved["version"], 2)
            self.assertEqual(len(page["audio_clips"]), 3)
            self.assertEqual(page["visual_clips"][0]["timeline_end"], 3.0)

    def test_v2_tracks_are_independent_and_compile_silence(self):
        audio = [
            {"id": "a1", "source_start": 0.0, "source_end": 1.0,
             "timeline_start": 0.0, "text": "一。"},
            {"id": "a2", "source_start": 1.0, "source_end": 2.0,
             "timeline_start": 1.5, "text": "二。"},
            {"id": "a3", "source_start": 2.0, "source_end": 3.0,
             "timeline_start": 2.5, "text": "三。"},
        ]
        visuals = [
            {"shot_id": "p0_a", "timeline_start": 0.0, "timeline_end": 2.2},
            {"shot_id": "p0_b", "timeline_start": 2.2, "timeline_end": 3.5},
        ]
        clean_audio, clean_visuals = validate_page_timeline(
            audio, visuals, ["p0_a", "p0_b"], 3.0)
        self.assertEqual(len(clean_audio), 3)
        self.assertEqual(len(clean_visuals), 2)

        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "pages"))
            Image.new("RGB", (90, 160), "white").save(os.path.join(root, "pages", "p0.png"))
            page_director = dict(self.director, shots=self.director["shots"][:2])
            updated, _, _ = apply_page_timeline(
                page_director, "p0.png", "audio/p0.wav", audio, visuals, 3.0)
            timeline = compile_script(updated, root=root)
            self.assertEqual(len(timeline["shots"]), 2)
            self.assertEqual(len(timeline["audio_tracks"]), 3)
            self.assertEqual([t["start"] for t in timeline["audio_tracks"]], [0.0, 1.5, 2.5])
            self.assertEqual([s["transition_out"] for s in timeline["shots"]], ["CUT", "CUT"])
            self.assertEqual([s["duration"] for s in timeline["shots"]], [2.2, 1.3])

    def test_v2_rejects_overlapping_audio_and_non_contiguous_visuals(self):
        audio = [
            {"source_start": 0.0, "source_end": 1.0, "timeline_start": 0.0},
            {"source_start": 1.0, "source_end": 2.0, "timeline_start": 0.8},
        ]
        visuals = [
            {"shot_id": "p0_a", "timeline_start": 0.0, "timeline_end": 1.0},
            {"shot_id": "p0_b", "timeline_start": 1.2, "timeline_end": 2.0},
        ]
        with self.assertRaisesRegex(ValueError, "重叠"):
            validate_page_timeline(audio, visuals, ["p0_a", "p0_b"], 2.0)
        audio[1]["timeline_start"] = 1.0
        with self.assertRaisesRegex(ValueError, "首尾连续"):
            validate_page_timeline(audio, visuals, ["p0_a", "p0_b"], 2.0)


if __name__ == "__main__":
    unittest.main()
