# -*- coding: utf-8 -*-
"""命令行入口。

典型流程（配音友好版）：
  1) python -m motion_comic manifest examples/director.json -o output/
       -> narration_manifest.json（每镜文本/音色/目标 wav 路径）
  2) 用任意 TTS（edge-tts / 智谱 / 火山 / 人声录音）把 manifest 里的 wav 补齐
  3) python -m motion_comic compile examples/director.json -o output/
       -> render_timeline.json + subtitles.srt + dubbing_sheet.md
  4) python -m motion_comic render output/render_timeline.json -o output/episode.mp4
       -> 成片（音轨按时间戳自动铺好）
"""
import argparse
import json
import os

from . import __version__
from .compiler import compile_script
from .renderer import render as render_video
from .subtitles import write_dubbing_sheet, write_narration_manifest, write_srt


def _load(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="motion-comic", description="动态漫引擎 CLI")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="生成 TTS 待合成清单（配音第一步）")
    m.add_argument("director")
    m.add_argument("-o", "--out", default="output")

    c = sub.add_parser("compile", help="director.json -> render_timeline.json + SRT + 配音表")
    c.add_argument("director")
    c.add_argument("-o", "--out", default="output")
    c.add_argument("--root", default=".", help="素材路径根目录（默认当前目录）")

    r = sub.add_parser("render", help="render_timeline.json -> mp4")
    r.add_argument("timeline")
    r.add_argument("-o", "--out", default="output/episode.mp4")
    r.add_argument("--root", default=".")

    a = ap.parse_args(argv)

    if a.cmd == "manifest":
        os.makedirs(a.out, exist_ok=True)
        items = write_narration_manifest(_load(a.director), os.path.join(a.out, "narration_manifest.json"))
        print(f"manifest: {len(items)} 条待合成 -> {os.path.join(a.out, 'narration_manifest.json')}")

    elif a.cmd == "compile":
        os.makedirs(a.out, exist_ok=True)
        tl = compile_script(_load(a.director), root=a.root)
        tp = os.path.join(a.out, "render_timeline.json")
        with open(tp, "w", encoding="utf-8") as fp:
            json.dump(tl, fp, ensure_ascii=False, indent=2)
        n = write_srt(tl, os.path.join(a.out, "subtitles.srt"))
        write_dubbing_sheet(tl, os.path.join(a.out, "dubbing_sheet.md"))
        print(f"compile: {len(tl['shots'])} 镜 / 总时长 {tl['total_duration']:.2f}s / {len(tl['audio_tracks'])} 条音轨")
        print(f"  -> {tp}")
        print(f"  -> subtitles.srt ({n} 条字幕) / dubbing_sheet.md")

    elif a.cmd == "render":
        info = render_video(a.timeline, a.out, root=a.root)
        print(f"render: {info['frames']} 帧 -> {info['output']}")


if __name__ == "__main__":
    main()
