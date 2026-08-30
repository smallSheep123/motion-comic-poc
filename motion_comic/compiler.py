# -*- coding: utf-8 -*-
"""Timeline Compiler：director.json(语义+归一化坐标) -> render_timeline.json(像素+秒)。

职责边界：
- 输入的 duration_policy=voice 时，用 ffprobe 实测音频时长 + pad 作为镜头时长
  （TTS 先行、时长实测，不信任任何"模型估算"）；
- 相机矩形由 motion preset + focus bbox 确定性解出：安全边距、页边界钳制、
  输出比例锁定、min/max 变焦限制，保证人脸不出屏、气泡不被裁；
- 产出全局时间戳：每镜 global_start、转场重叠窗口、字幕事件、音频铺轨起点。
"""
from __future__ import annotations

import os

from PIL import Image

from .audio import audio_duration
from .schema import ASPECTS, canonical_motion, validate_director

PAD_IN_DEFAULT = 0.2   # 语音相对镜头起点的提前量(秒)：留呼吸、也留混音余量
PAD_OUT_DEFAULT = 0.4  # 语音结束后镜头继续停留的时间
MIN_DURATION_DEFAULT = 2.0

# 运动速度归一化：观感均匀的关健。镜头时长由语音钉死，若不加约束，
# "长距离 + 短语音"的镜头会突然猛推，平移页则显得慢。
MAX_ZOOM_PER_SEC = 1.18   # 每秒最多允许的面积缩放倍率（>1）
MIN_MEANING_ZOOM = 1.06   # 全程变焦低于此倍率视为"蠕动"，降级 HOLD
MAX_PAN_PER_SEC = 0.55    # 每秒最多平移的窗口宽度（页内行程占比）

# "推到位就停"：运镜只在镜头开头一小段发生，落点后完全静止，
# 把稳定画面留给观众读内容。settle 段 = min(2.5s, 35% 时长)。
SETTLE_MAX_SEC = 2.5
SETTLE_MAX_FRAC = 0.35


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class CameraSolver:
    """单页上的相机矩形求解器。矩形 = [x0, y0, x1, y1] 像素（浮点，子像素精度）。"""

    def __init__(self, page_w, page_h, aspect, min_crop_frac=0.35, safe_margin=0.08):
        self.W, self.H, self.A = float(page_w), float(page_h), aspect
        self.max_w = min(self.W, self.H * self.A)   # 页内能取出的最大 9:16 窗
        self.max_h = self.max_w / self.A
        self.min_w = self.max_w * min_crop_frac     # 变焦上限（窗不能小于这个宽度）
        self.m = safe_margin

    def _rect(self, cx, cy, w) -> list[float]:
        w = _clamp(w, self.min_w, self.max_w)
        h = w / self.A
        cx = _clamp(cx, w / 2, self.W - w / 2)
        cy = _clamp(cy, h / 2, self.H - h / 2)
        return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]

    def full_rect(self, cx=None, cy=None) -> list[float]:
        """全景窗（尽量大的 9:16），中心默认页心，可朝 focus 偏移（会被钳制）。"""
        return self._rect(self.W / 2 if cx is None else cx,
                          self.H / 2 if cy is None else cy, self.max_w)

    def focus_rect(self, bbox_norm) -> list[float]:
        """包住 focus bbox(归一化) + 安全边距的最小合规窗；中心尽量对齐 bbox 中心。"""
        x0, y0, x1, y1 = [v * (self.W if i % 2 == 0 else self.H) for i, v in enumerate(bbox_norm)]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ew = (x1 - x0) * (1 + 2 * self.m)
        eh = (y1 - y0) * (1 + 2 * self.m)
        want = max(ew, eh * self.A)  # 宽高都要包住 → w>=ew 且 w/A>=eh
        return self._rect(cx, cy, want)

    def plain_rect(self, bbox_norm, margin=None) -> list[float]:
        """原比例矩形：包住 bbox + 边距，不锁定输出比例（供 letterbox 定格用）。"""
        m = self.m if margin is None else margin
        x0, y0, x1, y1 = [v * (self.W if i % 2 == 0 else self.H) for i, v in enumerate(bbox_norm)]
        ew = _clamp((x1 - x0) * (1 + 2 * m), 1.0, self.W)
        eh = _clamp((y1 - y0) * (1 + 2 * m), 1.0, self.H)
        cx = _clamp((x0 + x1) / 2, ew / 2, self.W - ew / 2)
        cy = _clamp((y0 + y1) / 2, eh / 2, self.H - eh / 2)
        return [cx - ew / 2, cy - eh / 2, cx + ew / 2, cy + eh / 2]

    def pan_rects(self, direction: str, cx=None):
        """扫读：窗口取到允许的最大，然后沿方向从一端走到另一端。
        页面不够长/宽时自动收缩窗口换取行程。"""
        vertical = direction in ("PAN_UP", "PAN_DOWN")
        w = self.max_w
        if vertical:
            if self.H - w / self.A < 0.15 * self.H:      # 高度方向行程不足
                w = min(w, 0.70 * self.H * self.A)
        else:
            if self.W - w < 0.15 * self.W:               # 宽度方向行程不足
                w = min(w, 0.70 * self.W)
        h = w / self.A
        cx = _clamp(self.W / 2 if cx is None else cx, w / 2, self.W - w / 2)
        top, bot = self._rect(cx, h / 2, w), self._rect(cx, self.H - h / 2, w)
        left = self._rect(w / 2, self.H / 2, w)
        right = self._rect(self.W - w / 2, self.H / 2, w)
        return {
            "PAN_DOWN": (top, bot), "PAN_UP": (bot, top),
            "PAN_LEFT": (right, left), "PAN_RIGHT": (left, right),
        }[direction]


def overlap_sec(transition: str, crossfade_sec: float) -> float:
    return 0.0 if transition == "CUT" else crossfade_sec


def resolve_duration(shot: dict, root: str) -> tuple[float, float]:
    """返回 (镜头时长, 语音实测时长或0)。优先级：fixed > voice+pad，最后不低于 min_duration。"""
    if shot.get("fixed_duration"):
        return float(shot["fixed_duration"]), 0.0
    narr = shot.get("narration") or {}
    wav = narr.get("audio")
    dur = 0.0
    if wav:
        p = os.path.join(root, wav)
        if os.path.exists(p):
            dur = audio_duration(p)
            pad_in = narr.get("pad_in", PAD_IN_DEFAULT)
            pad_out = narr.get("pad_out", PAD_OUT_DEFAULT)
            return max(dur + pad_in + pad_out, shot.get("min_duration", MIN_DURATION_DEFAULT)), dur
    return max(dur, shot.get("min_duration", MIN_DURATION_DEFAULT)), dur


def clamp_motion_speed(start, end, duration, aspect):
    """把 start->end 的运动钳到速度上限内（中心不变、收缩终点）。
    返回 (start, end, adjustment)；adjustment=None 表示未触发。"""
    w0 = start[2] - start[0]
    w1 = end[2] - end[0]
    zoom = (w0 / w1) ** 2  # 面积缩放倍率
    cx, cy = (end[0] + end[2]) / 2, (end[1] + end[3]) / 2
    if zoom > MAX_ZOOM_PER_SEC ** duration:
        # 终点向起点收缩到允许深度：求允许的终点窗宽 w1'
        w1_max = w0 / (MAX_ZOOM_PER_SEC ** (duration / 2))
        h1_max = w1_max / aspect
        return start, [cx - w1_max / 2, cy - h1_max / 2, cx + w1_max / 2, cy + h1_max / 2], \
            f"zoom {zoom:.2f}x/{duration:.1f}s 超速，收缩到 {zoom / (w0/w1_max) ** 2:.2f}x"
    dx = abs((end[0] + end[2]) - (start[0] + start[2])) / 2 / w0
    dy = abs((end[1] + end[3]) - (start[1] + start[3])) / 2 / (w0 / aspect)
    travel = max(dx, dy)
    if travel > MAX_PAN_PER_SEC * duration:
        k = MAX_PAN_PER_SEC * duration / travel
        # 起点不动、终点沿运动方向收缩行程
        ex = start[0] + ((end[0] - start[0]) * k)
        ey = start[1] + ((end[1] - start[1]) * k)
        w = end[2] - end[0]
        h = w / aspect
        return start, [ex, ey, ex + w, ey + h], f"pan {travel:.2f}窗/{duration:.1f}s 超速，行程 x{k:.2f}"
    return start, end, None


def compile_script(director: dict, root: str = ".") -> dict:
    validate_director(director)
    aspect = ASPECTS[director.get("aspect", "9:16")]
    cam_cfg = director.get("camera", {})
    min_frac = cam_cfg.get("min_crop_frac", 0.35)
    margin = cam_cfg.get("safe_margin", 0.08)
    out = {"width": 1080, "height": 1920, "fps": 30}
    out.update(director.get("output") or {})
    if abs((out["width"] / out["height"]) - aspect) > 1e-6:
        raise ValueError(f"output {out['width']}x{out['height']} 与 aspect {director.get('aspect')} 不一致")
    xf = float(director.get("crossfade_sec", 0.6))
    # 推拉运镜默认禁用（产品决策：放大必然挤压周边内容，观感不可控）。
    # 显式 "allow_zoom": true 才启用；禁用时推拉一律规范化为定格。
    allow_zoom = bool(director.get("allow_zoom", False))

    shots_out, audio_tracks = [], []
    g = 0.0  # 当前全局时间戳
    for idx, sh in enumerate(director["shots"]):
        page_path = os.path.join(root, sh["page"])
        if not os.path.exists(page_path):
            raise FileNotFoundError(f"shot[{idx}] 页面不存在: {page_path}")
        W, H = Image.open(page_path).size
        solver = CameraSolver(W, H, aspect, min_frac, margin)

        motion = canonical_motion(sh.get("motion", "HOLD"))
        focus = sh.get("focus")
        narr = sh.get("narration") or {}
        pin_fit = None
        # 推拉必须有"叙事对象"：narration.subject（当前语音对应的画面主体，
        # 如说话人头部/气泡/动作核心）优先于页级 focus.bbox。
        subject = narr.get("subject") or (focus or {}).get("bbox")
        if motion in ("SLOW_PUSH", "SLOW_PULL") and (not subject or not allow_zoom):
            # 无对象不推镜；推镜未启用时降级为定格（有 subject 定格该格）
            motion = "HOLD"

        if motion == "HOLD":
            # HOLD 支持两种定格：格级 letterbox（有 subject——窗口=该格原比例，
            # 已由视觉模型+墨线吸附贴合格线，margin 仅留极小呼吸，绝不侵入邻格）
            if subject:
                start = end = solver.plain_rect(subject, margin=0.015)
                pin_fit = "letterbox"
            else:
                start = end = solver.full_rect()
                pin_fit = None
        elif motion in ("SLOW_PUSH", "SLOW_PULL"):
            fr = solver.focus_rect(subject)
            full = solver.full_rect(cx=(subject[0] + subject[2]) / 2 * W,
                                    cy=(subject[1] + subject[3]) / 2 * H)
            start, end = (full, fr) if motion == "SLOW_PUSH" else (fr, full)
            w0, w1 = start[2] - start[0], end[2] - end[0]
            if (w0 / w1) ** 2 < MIN_MEANING_ZOOM:
                start = end = solver.full_rect(cx=(start[0] + start[2]) / 2)  # 蠕动降级 HOLD
                motion = "HOLD"
        else:  # PAN_*（长图扫读，全程缓动浏览，不受 settle 规则影响）
            start, end = solver.pan_rects(motion)

        duration, speech_dur = resolve_duration(sh, root)
        adjustment = None
        if motion != "HOLD":
            start, end, adjustment = clamp_motion_speed(start, end, duration, aspect)

        # "推到位就停"：只对推拉生成 settle 点，PAN/HOLD 不生成
        settle_at = None
        if motion in ("SLOW_PUSH", "SLOW_PULL"):
            settle_at = round(min(SETTLE_MAX_SEC, SETTLE_MAX_FRAC * duration) / duration, 3)
        transition = sh.get("transition_out", "CUT") if idx < len(director["shots"]) - 1 else "CUT"
        if duration <= overlap_sec(transition, xf) + 0.2:
            raise ValueError(f"shot[{idx}] 时长 {duration:.2f}s 不足以容纳转场，检查音频/时长设置")

        narr_out = None
        if narr.get("text"):
            pad_in = narr.get("pad_in", PAD_IN_DEFAULT)
            narr_out = {
                "text": narr["text"],
                "voice": narr.get("voice", "narrator"),
                "audio": narr.get("audio"),
                "speech_start": round(g + pad_in, 3),
                "speech_dur": round(speech_dur, 3),
            }
            if narr.get("audio") and speech_dur > 0:
                audio_tracks.append({"shot_id": sh.get("id", f"shot_{idx+1:03d}"),
                                     "file": narr["audio"], "start": round(g + pad_in, 3)})

        shots_out.append({
            "id": sh.get("id", f"shot_{idx+1:03d}"),
            "page": sh["page"], "page_size": [W, H],
            "motion": motion, "transition_out": transition,
            "global_start": round(g, 3), "duration": round(duration, 3),
            "start_rect": [round(v, 2) for v in start],
            "end_rect": [round(v, 2) for v in end],
            "narration": narr_out,
            "reason": (focus or {}).get("reason", ""),
            "motion_note": adjustment,
            "settle_at": settle_at,
            "fit": pin_fit,
        })
        g += duration - overlap_sec(transition, xf)

    total = shots_out[-1]["global_start"] + shots_out[-1]["duration"]
    return {
        "version": 1,
        "output": out,
        "crossfade_sec": xf,
        "supersample": int(director.get("supersample", 2)),
        "total_duration": round(total, 3),
        "shots": shots_out,
        "audio_tracks": audio_tracks,
    }
