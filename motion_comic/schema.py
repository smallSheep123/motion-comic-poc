# -*- coding: utf-8 -*-
"""director.json 的词表与校验。

设计原则（来自评审共识）：
- AI/视觉模型只产出"语义级导演指令"，坐标一律 0~1 归一化，与图片分辨率解耦；
- 运镜与转场是封闭白名单，模型做选择题，不做自由发挥；
- 具体像素矩形、时长、时间戳全部由 compiler 用确定性规则计算。
"""

import math

ASPECTS = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0, "4:5": 4 / 5}

# 规范运镜（语义别名 -> 规范名）
_CANON = {
    "FAST_PUSH": "SLOW_PUSH",   # 速度由时长涌现，镜头几何相同
    "FOCUS_FACE": "SLOW_PUSH",
    "FOCUS_OBJECT": "SLOW_PUSH",
    "REVEAL_DOWN": "PAN_DOWN",
}
MOTIONS = ["HOLD", "SLOW_PUSH", "SLOW_PULL",
           "PAN_UP", "PAN_DOWN", "PAN_LEFT", "PAN_RIGHT"]
MOTION_ALIASES = dict(_CANON)

TRANSITIONS = ["CUT", "CROSSFADE", "FADE_BLACK", "FADE_WHITE"]

DURATION_POLICIES = ["voice", "fixed"]


def canonical_motion(name: str) -> str:
    return _CANON.get(name, name)


def validate_director(d: dict) -> None:
    """轻校验：词表内、bbox 在 0~1。文件存在性交给 compiler 报错。"""
    if not isinstance(d.get("shots"), list) or not d["shots"]:
        raise ValueError("director.json 需要 non-empty 'shots' 数组")
    if d.get("aspect", "9:16") not in ASPECTS:
        raise ValueError(f"未知 aspect: {d.get('aspect')}，可选 {list(ASPECTS)}")
    for i, sh in enumerate(d["shots"]):
        m = canonical_motion(sh.get("motion", "HOLD"))
        if m not in MOTIONS:
            raise ValueError(f"shot[{i}] 未知 motion: {sh.get('motion')}，可选 {MOTIONS + list(MOTION_ALIASES)}")
        tr = sh.get("transition_out", "CUT")
        if tr not in TRANSITIONS:
            raise ValueError(f"shot[{i}] 未知 transition: {tr}，可选 {TRANSITIONS}")
        tr_sec = sh.get("transition_sec")
        if tr_sec is not None and (not isinstance(tr_sec, (int, float)) or
                                   not math.isfinite(tr_sec) or tr_sec < 0):
            raise ValueError(f"shot[{i}] transition_sec 必须是非负有限数字")
        f = sh.get("focus")
        if f:
            bbox = f.get("bbox")
            if not (isinstance(bbox, list) and len(bbox) == 4):
                raise ValueError(f"shot[{i}] focus.bbox 必须是 [x0,y0,x1,y1] 四元组")
            if not all(isinstance(v, (int, float)) and 0.0 <= v <= 1.0 for v in bbox):
                raise ValueError(f"shot[{i}] focus.bbox 必须全部在 0~1（归一化坐标）")
            if not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
                raise ValueError(f"shot[{i}] focus.bbox 需要 x0<x1, y0<y1")
