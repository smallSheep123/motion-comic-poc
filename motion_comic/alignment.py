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

ALIGNMENT_VERSION = 2
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


def validate_page_timeline(audio_clips: list[dict], visual_clips: list[dict],
                           shot_ids: list[str], audio_duration: float) -> tuple[list[dict], list[dict]]:
    """Validate v2's independent audio and visual tracks."""
    duration = _finite_number(audio_duration, "audio_duration")
    if duration <= 0:
        raise ValueError("audio_duration 必须大于 0")
    if not isinstance(audio_clips, list) or not audio_clips:
        raise ValueError("audio_clips 至少需要一个片段")
    clean_audio = []
    previous_timeline_end = 0.0
    for index, raw in enumerate(audio_clips):
        source_start = _finite_number(raw.get("source_start"), f"audio_clips[{index}].source_start")
        source_end = _finite_number(raw.get("source_end"), f"audio_clips[{index}].source_end")
        timeline_start = _finite_number(raw.get("timeline_start"), f"audio_clips[{index}].timeline_start")
        if source_start < 0 or source_end - source_start < MIN_SEGMENT_SEC:
            raise ValueError(f"audio_clips[{index}] 的源音频入点/出点无效")
        if source_end > duration + 0.05:
            raise ValueError(f"audio_clips[{index}] 的出点超过源音频时长")
        if timeline_start < previous_timeline_end - 1e-3:
            raise ValueError(f"audio_clips[{index}] 与前一音频片段重叠")
        clip_duration = source_end - source_start
        clean_audio.append({
            "id": str(raw.get("id") or f"audio_{index + 1:02d}"),
            "source_start": round(source_start, 3),
            "source_end": round(source_end, 3),
            "timeline_start": round(timeline_start, 3),
            "text": str(raw.get("text") or ""),
        })
        previous_timeline_end = timeline_start + clip_duration

    if not isinstance(visual_clips, list) or len(visual_clips) != len(shot_ids):
        raise ValueError("visual_clips 数量必须与本页画面数量一致")
    clean_visual = []
    previous_end = 0.0
    for index, (raw, shot_id) in enumerate(zip(visual_clips, shot_ids)):
        if raw.get("shot_id") != shot_id:
            raise ValueError(f"visual_clips[{index}] 的 shot_id 顺序不匹配")
        start = _finite_number(raw.get("timeline_start"), f"visual_clips[{index}].timeline_start")
        end = _finite_number(raw.get("timeline_end"), f"visual_clips[{index}].timeline_end")
        if index == 0 and abs(start) > 1e-3:
            raise ValueError("第一张画面必须从 0 秒开始")
        if index > 0 and abs(start - previous_end) > 0.02:
            raise ValueError("画面片段必须首尾连续")
        if end - start < MIN_SEGMENT_SEC:
            raise ValueError(f"visual_clips[{index}] 时长过短")
        clean_visual.append({
            "shot_id": shot_id,
            "timeline_start": round(start, 3),
            "timeline_end": round(end, 3),
        })
        previous_end = end
    return clean_audio, clean_visual


def apply_page_timeline(director: dict, page_name: str, audio_path: str,
                        audio_clips: list[dict], visual_clips: list[dict],
                        audio_duration: float) -> tuple[dict, list[dict], list[dict]]:
    """Apply independent v2 tracks while keeping the existing shot renderer.

    Visual clips remain contiguous and drive shot durations.  Audio clips are
    stored at the director top level and anchored to the first shot of the page,
    so inserted silence and extra splits no longer need a one-to-one shot map.
    """
    result = deepcopy(director)
    indexed = [(index, shot) for index, shot in enumerate(result.get("shots", []))
               if os.path.basename(shot.get("page", "")) == page_name]
    if not indexed:
        raise ValueError(f"director.json 中找不到页面 {page_name}")
    shot_ids = [shot.get("id") for _, shot in indexed]
    clean_audio, clean_visual = validate_page_timeline(
        audio_clips, visual_clips, shot_ids, audio_duration)
    audio_end = max(clip["timeline_start"] + clip["source_end"] - clip["source_start"]
                    for clip in clean_audio)
    effective_page_end = max(clean_visual[-1]["timeline_end"], audio_end)
    clean_visual[-1]["timeline_end"] = round(effective_page_end, 3)

    for (_, shot), visual in zip(indexed, clean_visual):
        narration = shot.get("narration") or {}
        narration["timeline_managed"] = True
        shot["narration"] = narration
        shot["fixed_duration"] = round(visual["timeline_end"] - visual["timeline_start"], 3)
        shot["transition_out"] = "CUT"
        shot["transition_sec"] = 0.0
        shot.pop("gap_after", None)

    anchor_shot_id = shot_ids[0]
    tracks = [track for track in result.get("audio_timeline", [])
              if track.get("page") != page_name]
    for clip in clean_audio:
        tracks.append({
            "id": clip["id"],
            "page": page_name,
            "anchor_shot_id": anchor_shot_id,
            "file": audio_path,
            "start_offset": clip["timeline_start"],
            "source_start": clip["source_start"],
            "source_duration": round(clip["source_end"] - clip["source_start"], 3),
            "text": clip["text"],
        })
    result["audio_timeline"] = tracks
    return result, clean_audio, clean_visual
