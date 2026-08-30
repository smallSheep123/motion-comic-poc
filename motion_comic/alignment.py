# -*- coding: utf-8 -*-
"""Non-destructive page-audio to panel timeline alignment helpers.

The alignment editor stores source-audio in/out points separately from the
original media.  This module translates that edit decision list into the
existing ``director.json`` format consumed by the compiler.
"""
from __future__ import annotations

import math
import os
from copy import deepcopy

from .schema import TRANSITIONS

ALIGNMENT_VERSION = 1
MIN_SEGMENT_SEC = 0.08
MAX_GAP_SEC = 30.0
MAX_TRANSITION_SEC = 10.0


def _finite_number(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数字")
    return number


def validate_segments(segments: list[dict], shot_ids: list[str], audio_duration: float | None = None) -> list[dict]:
    """Validate and normalize a page's ordered panel/audio mappings."""
    if not isinstance(segments, list) or len(segments) != len(shot_ids):
        raise ValueError("segments 数量必须与本页画面片段数量一致")
    duration = None if audio_duration is None else _finite_number(audio_duration, "audio_duration")
    normalized = []
    previous_end = 0.0
    for index, (raw, expected_id) in enumerate(zip(segments, shot_ids)):
        if raw.get("shot_id") != expected_id:
            raise ValueError(f"segments[{index}] 的 shot_id 顺序不匹配")
        source_start = _finite_number(raw.get("source_start"), f"segments[{index}].source_start")
        source_end = _finite_number(raw.get("source_end"), f"segments[{index}].source_end")
        gap_after = _finite_number(raw.get("gap_after", 0), f"segments[{index}].gap_after")
        transition = raw.get("transition_out", "CUT")
        transition_sec = _finite_number(raw.get("transition_duration", 0),
                                        f"segments[{index}].transition_duration")
        if source_start < -1e-6 or source_end - source_start < MIN_SEGMENT_SEC:
            raise ValueError(f"segments[{index}] 的音频入点/出点无效")
        if source_start < previous_end - 1e-3:
            raise ValueError(f"segments[{index}] 与前一段音频重叠")
        if duration is not None and source_end > duration + 0.05:
            raise ValueError(f"segments[{index}] 的出点超过音频总时长")
        if not 0 <= gap_after <= MAX_GAP_SEC:
            raise ValueError(f"segments[{index}] 的留白需在 0~{MAX_GAP_SEC:g}s")
        if transition not in TRANSITIONS:
            raise ValueError(f"segments[{index}] 的转场类型无效")
        if transition == "CUT":
            transition_sec = 0.0
        elif not 0.05 <= transition_sec <= MAX_TRANSITION_SEC:
            raise ValueError(f"segments[{index}] 的转场时长需在 0.05~{MAX_TRANSITION_SEC:g}s")
        elif transition_sec > (source_end - source_start) + gap_after - 0.2:
            raise ValueError(f"segments[{index}] 的转场长于当前画面可用时长")
        normalized.append({
            "shot_id": expected_id,
            "source_start": round(source_start, 3),
            "source_end": round(source_end, 3),
            "gap_after": round(gap_after, 3),
            "transition_out": transition,
            "transition_duration": round(transition_sec, 3),
        })
        previous_end = source_end
    return normalized


def apply_page_alignment(director: dict, page_name: str, audio_path: str,
                         segments: list[dict], audio_duration: float | None = None) -> dict:
    """Return a director copy with one page's edit decisions applied.

    ``fixed_duration`` includes the visual transition overlap.  Therefore the
    compiler advances the next shot by ``speech + requested silence`` while the
    transition occupies the final part of the current visual clip.
    """
    result = deepcopy(director)
    indexed = [(i, sh) for i, sh in enumerate(result.get("shots", []))
               if os.path.basename(sh.get("page", "")) == page_name]
    if not indexed:
        raise ValueError(f"director.json 中找不到页面 {page_name}")
    shot_ids = [sh.get("id") for _, sh in indexed]
    clean = validate_segments(segments, shot_ids, audio_duration)
    last_director_index = len(result["shots"]) - 1

    for (director_index, shot), segment in zip(indexed, clean):
        narration = shot.get("narration") or {}
        if not narration.get("text"):
            narration["text"] = ""
        source_duration = round(segment["source_end"] - segment["source_start"], 3)
        transition = segment["transition_out"]
        transition_sec = segment["transition_duration"]
        if director_index == last_director_index:
            transition = "CUT"
            transition_sec = 0.0
        narration.update({
            "audio": audio_path,
            "source_start": segment["source_start"],
            "source_duration": source_duration,
            "pad_in": 0.0,
            "pad_out": 0.0,
        })
        shot["narration"] = narration
        shot["transition_out"] = transition
        shot["transition_sec"] = transition_sec
        shot["gap_after"] = segment["gap_after"]
        shot["fixed_duration"] = round(
            source_duration + segment["gap_after"] + transition_sec, 3)
    return result
