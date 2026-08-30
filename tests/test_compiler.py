# -*- coding: utf-8 -*-
"""Compiler 回归测试：相机数学与时间轴的确定性验证（纯逻辑，不碰 ffmpeg）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motion_comic.compiler import CameraSolver, compile_script  # noqa: E402
from motion_comic.schema import ASPECTS  # noqa: E402
from motion_comic.subtitles import proportional_events, subtitle_events  # noqa: E402

A = ASPECTS["9:16"]


def shot(**kw):
    base = {"id": "s", "page": "pages/page1.png", "motion": "HOLD",
            "transition_out": "CUT", "fixed_duration": 3.0}
    base.update(kw)
    return base


def director(shots):
    return {"aspect": "9:16", "shots": shots}


class TestCameraSolver(unittest.TestCase):
    def setUp(self):
        self.solver = CameraSolver(1200, 1800, A)

    def test_aspect_locked(self):
        for motion in ("SLOW_PUSH", "SLOW_PULL"):
            r = self.solver.focus_rect([0.6, 0.3, 0.95, 0.6])
            self.assertAlmostEqual((r[2] - r[0]) / (r[3] - r[1]), A, places=6,
                                   msg=f"{motion} 输出矩形必须锁定 9:16")

    def test_clamped_in_page(self):
        r = self.solver.focus_rect([0.9, 0.02, 0.99, 0.1])  # 极角 focus
        self.assertGreaterEqual(r[0], -0.01)
        self.assertGreaterEqual(r[1], -0.01)
        self.assertLessEqual(r[2], 1200.01)
        self.assertLessEqual(r[3], 1800.01)

    def test_focus_contained_with_margin(self):
        bbox = [0.55, 0.35, 0.9, 0.62]
        r = self.solver.focus_rect(bbox)
        self.assertLessEqual(r[0], bbox[0] * 1200, msg="起点应在 focus 左侧")
        self.assertGreaterEqual(r[2], bbox[2] * 1200, msg="终点应在 focus 右侧")
        self.assertLessEqual(r[1], bbox[1] * 1800)
        self.assertGreaterEqual(r[3], bbox[3] * 1800)

    def test_push_without_focus_degrades_to_hold(self):
        tl = compile_script(director([shot(motion="SLOW_PUSH")]), root=".")
        self.assertEqual(tl["shots"][0]["motion"], "HOLD")

    def test_push_with_subject_settles(self):
        d = director([shot(motion="SLOW_PUSH", fixed_duration=10.0,
                           narration={"text": "他说话了。",
                                      "subject": [0.55, 0.30, 0.75, 0.55]})])
        d["allow_zoom"] = True  # 推镜默认禁用，此用例显式开启
        tl = compile_script(d, root=".")
        s = tl["shots"][0]
        self.assertEqual(s["motion"], "SLOW_PUSH")
        self.assertIsNotNone(s["settle_at"], msg="推镜必须带 settle 点（推到位就停）")
        self.assertAlmostEqual(s["settle_at"], 0.25, places=2,
                               msg="10s 镜头 settle 应为 2.5s/10s")
        # 终点窗必须完整包住 subject（说话对象永不裁边）
        x0, y0, x1, y1 = s["end_rect"]
        sx0, sy0 = 0.55 * s["page_size"][0], 0.30 * s["page_size"][1]
        sx1, sy1 = 0.75 * s["page_size"][0], 0.55 * s["page_size"][1]
        self.assertLessEqual(x0, sx0 + 1)
        self.assertLessEqual(y0, sy0 + 1)
        self.assertGreaterEqual(x1, sx1 - 1)
        self.assertGreaterEqual(y1, sy1 - 1)

    def test_pan_gets_no_settle(self):
        tl = compile_script(director([shot(motion="PAN_DOWN", fixed_duration=5.0),
                                      shot()]), root=".")
        self.assertIsNone(tl["shots"][0]["settle_at"],
                          msg="长图扫读是全程浏览语义，不应被 settle 截断")

    def test_hold_with_subject_pins_region(self):
        tl = compile_script(director([
            shot(motion="HOLD", fixed_duration=4.0,
                 narration={"text": "看这里。", "subject": [0.10, 0.05, 0.60, 0.40]}),
        ]), root=".")
        s = tl["shots"][0]
        self.assertEqual(s["fit"], "letterbox",
                         msg="格级定格必须走 letterbox（原比例留边，不裁切放大）")
        self.assertEqual(s["start_rect"], s["end_rect"],
                         msg="定格镜头起止窗必须完全一致（零运动）")
        x0, y0, x1, y1 = s["end_rect"]
        sx0, sy0 = 0.10 * s["page_size"][0], 0.05 * s["page_size"][1]
        sx1, sy1 = 0.60 * s["page_size"][0], 0.40 * s["page_size"][1]
        self.assertLessEqual(x0, sx0 + 1)
        self.assertLessEqual(y0, sy0 + 1)
        self.assertGreaterEqual(x1, sx1 - 1)
        self.assertGreaterEqual(y1, sy1 - 1)

    def test_letterbox_rect_keeps_native_ratio(self):
        # 横宽格 [0.05,0.10,0.95,0.35]（宽:高 ≈ 3.6:1）在 9:16 画布上必须保持原比例，
        # 不能被锁定成 9:16 后把窗口撑满整页
        tl = compile_script(director([
            shot(motion="HOLD", fixed_duration=4.0,
                 narration={"text": "横格。", "subject": [0.05, 0.10, 0.95, 0.35]}),
        ]), root=".")
        r = tl["shots"][0]["end_rect"]
        w, h = r[2] - r[0], r[3] - r[1]
        self.assertGreater(w / h, 2.0, msg="letterbox 矩形必须保持格子原比例")
        self.assertLess(w, tl["shots"][0]["page_size"][0] + 1)

    def test_zoom_disabled_by_default_degrades_to_pin(self):
        # 默认 allow_zoom=False：推拉一律规范化为定格（有 subject/focus 则定格该格）
        tl = compile_script(director([shot(motion="SLOW_PUSH", fixed_duration=3.0,
                                           focus={"bbox": [0.30, 0.30, 0.70, 0.70]})]), root=".")
        s = tl["shots"][0]
        self.assertEqual(s["motion"], "HOLD")
        self.assertEqual(s["start_rect"], s["end_rect"], msg="定格必须零运动")
        self.assertIsNone(s["settle_at"])

    def test_zoom_opt_in_restores_push(self):
        d = director([shot(motion="SLOW_PUSH", fixed_duration=6.0,
                           narration={"text": "推。", "subject": [0.55, 0.30, 0.75, 0.55]})])
        d["allow_zoom"] = True
        tl = compile_script(d, root=".")
        s = tl["shots"][0]
        self.assertEqual(s["motion"], "SLOW_PUSH", msg="显式 allow_zoom=true 才恢复推镜")
        self.assertIsNotNone(s["settle_at"])

    def test_pan_travel_on_tall_page(self):
        s = CameraSolver(800, 6000, A)
        start, end = s.pan_rects("PAN_DOWN")
        self.assertLess(start[1], end[1], msg="PAN_DOWN 应从上往下走")
        self.assertGreater(end[1] - start[1], 1000, msg="长条页应有足够行程")

    def test_speed_clamp_limits_zoom(self):
        from motion_comic.compiler import clamp_motion_speed, MAX_ZOOM_PER_SEC
        start = [0.0, 0.0, 1000.0, 1777.8]
        end = [400.0, 700.0, 600.0, 1155.6]  # 猛推：面积比 ~25x
        s, e, note = clamp_motion_speed(start, end, 3.0, A)
        self.assertIsNotNone(note, msg="超速猛推必须被钳制")
        got = ((s[2]-s[0]) / (e[2]-e[0])) ** 2
        self.assertLessEqual(got, MAX_ZOOM_PER_SEC ** 3.0 + 0.01,
                             msg="钳制后每秒变焦不得超过上限")

    def test_tiny_zoom_degrades_to_hold(self):
        tl = compile_script(director([
            # focus 几乎是整页 → 全景推到它 ≈ 没动，属"蠕动"
            shot(motion="SLOW_PUSH", fixed_duration=6.0,
                 focus={"bbox": [0.04, 0.03, 0.96, 0.96]}),
        ]), root=".")
        self.assertEqual(tl["shots"][0]["motion"], "HOLD",
                         msg="变焦幅度小于阈值应降级 HOLD，避免蠕动")


class TestTimeline(unittest.TestCase):
    def test_n_shot_timing_with_mixed_transitions(self):
        tl = compile_script(director([
            shot(id="a", fixed_duration=2.0, transition_out="CUT"),
            shot(id="b", fixed_duration=3.0, transition_out="CROSSFADE"),
            shot(id="c", fixed_duration=4.0),
        ]), root=".")
        g = [s["global_start"] for s in tl["shots"]]
        self.assertEqual(g, [0.0, 2.0, 4.4])  # 2.0 + 3.0 - 0.6 叠化重叠
        self.assertAlmostEqual(tl["total_duration"], 8.4)

    def test_last_shot_transition_forced_cut(self):
        tl = compile_script(director([shot(), shot(transition_out="FADE_WHITE")]), root=".")
        self.assertEqual(tl["shots"][-1]["transition_out"], "CUT")

    def test_duration_floor(self):
        tl = compile_script(director([shot(fixed_duration=None, min_duration=2.5)]), root=".")
        # fixed_duration=None 走 min_duration 兜底
        self.assertGreaterEqual(tl["shots"][0]["duration"], 2.5)

    def test_rejects_oversized_transition(self):
        with self.assertRaises(ValueError):
            compile_script(director([
                shot(fixed_duration=0.3, transition_out="CROSSFADE"),
                shot(),
            ]), root=".")


class TestSubtitles(unittest.TestCase):
    def test_proportional_split(self):
        evs = proportional_events("第一句。第二句比较长一些。", 1.0, 4.0)
        self.assertEqual(len(evs), 2)
        self.assertAlmostEqual(evs[0][0], 1.0)
        self.assertAlmostEqual(evs[1][1], 5.0)
        self.assertLess(evs[0][1], evs[1][0] + 1e-9, msg="事件不应重叠")

    def test_events_monotonic_and_in_range(self):
        tl = compile_script(director([
            shot(id="a", fixed_duration=3.0,
                narration={"text": "你好。世界很大。", "audio": "audio/不存在的文件.wav"}),
        ]), root=".")
        evs = subtitle_events(tl)
        self.assertEqual(len(evs), 2)
        for e in evs:
            self.assertGreaterEqual(e["start"], 0)
            self.assertLessEqual(e["end"], tl["total_duration"] + 1e-6)
        self.assertEqual([e["text"] for e in evs], ["你好。", "世界很大。"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
