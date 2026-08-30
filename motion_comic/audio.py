# -*- coding: utf-8 -*-
"""音频工具：ffprobe 实测时长——所有时间轴的最终事实来源，不靠模型估。"""
import json
import subprocess


def audio_duration(path: str) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(p.stdout)["format"]["duration"])
