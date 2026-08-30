# -*- coding: utf-8 -*-
"""字幕与配音导出：同一份 render_timeline 派生 SRT / 配音表 / TTS 清单。

关键取舍：长句按标点切分后，在各句之间按"字数比例"分配该镜语音实测时长——
边界绝对精确（因为整段时长是 ffprobe 实测的），句内比例分配误差远小于 0.5s，
人眼无感，却避免了引入 whisper 级强制对齐的复杂度。
"""
from __future__ import annotations

import json
import re


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?…；;])\s*", text.strip())
    return [p for p in parts if p]


def proportional_events(text: str, t0: float, dur: float) -> list[tuple[float, float, str]]:
    """把 [t0, t0+dur] 按字数比例切成每句一个事件。"""
    sents = split_sentences(text)
    if not sents:
        return []
    if len(sents) == 1:
        return [(t0, t0 + dur, sents[0])]
    weights = [len(s) for s in sents]
    total = sum(weights)
    events, acc = [], t0
    for s, w in zip(sents, weights):
        d = dur * w / total
        events.append((acc, acc + d, s))
        acc += d
    return events


def subtitle_events(timeline: dict) -> list[dict]:
    """从 render_timeline 提取字幕事件（有语音用语音窗，无语音用整镜窗）。"""
    evs = []
    for sh in timeline["shots"]:
        n = sh.get("narration")
        if not n or not n.get("text"):
            continue
        if n.get("speech_dur", 0) > 0:
            t0, dur = n["speech_start"], n["speech_dur"]
        else:
            t0, dur = sh["global_start"], sh["duration"]
        for s, e, txt in proportional_events(n["text"], t0, dur):
            evs.append({"start": round(s, 3), "end": round(e, 3), "text": txt})
    evs.sort(key=lambda x: x["start"])
    return evs


def fmt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(timeline: dict, path: str) -> int:
    evs = subtitle_events(timeline)
    lines = []
    for i, e in enumerate(evs, 1):
        lines += [str(i), f"{fmt_ts(e['start'])} --> {fmt_ts(e['end'])}", e["text"], ""]
    with open(path, "w", encoding="utf-8-sig") as fp:  # BOM：剪映/部分播放器更稳
        fp.write("\n".join(lines))
    return len(evs)


def write_dubbing_sheet(timeline: dict, path: str) -> None:
    """人工配音/审听用的时间戳总表（Markdown）。"""
    rows = ["| # | 开始 | 结束 | 时长 | 音色 | 音频 | 台词 |",
            "|---|------|------|------|------|------|------|"]
    for i, sh in enumerate(timeline["shots"], 1):
        n = sh.get("narration") or {}
        g0, g1 = sh["global_start"], sh["global_start"] + sh["duration"]
        rows.append(f"| {i} | {fmt_ts(g0)[:11]} | {fmt_ts(g1)[:11]} | {sh['duration']:.1f}s "
                    f"| {n.get('voice', '-')} | {n.get('audio') or '-'} | {n.get('text', '-')} |")
    head = [f"# 配音表  总时长 {timeline['total_duration']:.1f}s / {len(timeline['shots'])} 镜", ""]
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(head + rows) + "\n")


def write_narration_manifest(director: dict, path: str) -> list[dict]:
    """TTS 之前生成：每镜该合成什么文本、用什么音色、存到哪个文件。
    TTS 步骤（任意引擎）只需把这些 wav 补齐，再跑 compile 即可。"""
    items = []
    for idx, sh in enumerate(director["shots"]):
        n = sh.get("narration") or {}
        if not n.get("text"):
            continue
        items.append({
            "shot_id": sh.get("id", f"shot_{idx+1:03d}"),
            "text": n["text"],
            "voice": n.get("voice", "narrator"),
            "audio": n.get("audio") or f"audio/shot_{idx+1:03d}.wav",
        })
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(items, fp, ensure_ascii=False, indent=2)
    return items
