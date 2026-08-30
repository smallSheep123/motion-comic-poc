# -*- coding: utf-8 -*-
"""切割 + 出片：读 panels_final.json（编辑器产物）。

1) 把每格按最终框切割成图片 → <work>/panels_cut/
2) 自动生成 director.json：每格一镜定格(letterbox)，解说词=编辑器里写的文本
3) 串联管线：manifest → TTS → compile → render → <work>/output/episode.mp4

用法：python gui/cut_and_render.py --work real_manga2 [--skip-render]
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402


def cut_panels(work: str) -> list[dict]:
    final = json.load(open(os.path.join(work, "panels_final.json"), encoding="utf-8"))
    cut_dir = os.path.join(work, "panels_cut")
    os.makedirs(cut_dir, exist_ok=True)
    shots = []
    pages = sorted(final.keys())
    for pi, page in enumerate(pages):
        img = Image.open(os.path.join(work, "pages", page)).convert("RGB")
        W, H = img.size
        boxes = final[page]
        for gi, p in enumerate(boxes):
            bbox = p["bbox"]
            px = [round(v * (W if i % 2 == 0 else H)) for i, v in enumerate(bbox)]
            px = [max(px[0], 0), max(px[1], 0), min(px[2], W), min(px[3], H)]
            if px[2] - px[0] < 30 or px[3] - px[1] < 30:
                continue
            sid = f"shot_{pi:02d}_{gi:02d}"
            crop = img.crop(tuple(px))
            crop.save(os.path.join(cut_dir, f"{sid}.png"))
            text = (p.get("text") or "").strip()
            shot = {
                "id": sid, "page": f"pages/{page}",
                "focus": {"bbox": bbox, "reason": "人工校准"},
                "motion": "HOLD", "transition_out": "CUT" if gi > 0 else "CROSSFADE",
            }
            if text:
                shot["narration"] = {"text": text, "voice": "narrator",
                                     "audio": os.path.join(work, "audio", f"{sid}.mp3"),
                                     "subject": bbox}
            else:
                shot["fixed_duration"] = 3.5
            shots.append(shot)
    return shots


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"命令失败: {' '.join(cmd)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--skip-render", action="store_true", help="只切割+生成 director，不出片")
    ap.add_argument("--tts-voice", default=None,
                    help="kokoro 引擎的音色 ID（如 zf_001~zf_099 女声 / zm_009~zm_100 男声）")
    ap.add_argument("--tts", choices=["edge", "sovits", "f5", "kokoro", "none"], default="edge",
                    help="配音引擎：edge=连续合成 / sovits=GPT-SoVITS / f5=F5-TTS克隆 / kokoro=预置音色库")
    args = ap.parse_args()
    work = args.work
    if not os.path.exists(os.path.join(work, "panels_final.json")):
        sys.exit("未找到 panels_final.json——请先在编辑器里保存每一页")

    shots = cut_panels(work)
    director = {
        "aspect": "9:16", "crossfade_sec": 0.5, "supersample": 1,
        "camera": {"min_crop_frac": 0.45, "safe_margin": 0.08},
        "shots": shots,
    }
    dj = os.path.join(work, "director.json")
    with open(dj, "w", encoding="utf-8") as f:
        json.dump(director, f, ensure_ascii=False, indent=2)
    n_text = sum(1 for s in shots if "narration" in s)
    print(f"切割完成：{len(shots)} 格 → director.json（{n_text} 镜带解说词）")

    if args.skip_render:
        return
    run([sys.executable, "-m", "motion_comic", "manifest", dj, "-o", os.path.join(work, "output")])
    if args.tts != "none":
        tts_cmd = [sys.executable, os.path.join("examples", "make_audio.py"), dj, "--engine", args.tts]
        if args.tts_voice:
            tts_cmd += ["--voice", args.tts_voice]
        run(tts_cmd)
    run([sys.executable, "-m", "motion_comic", "compile", dj, "-o", os.path.join(work, "output"), "--root", work])
    out = os.path.join(work, "output", "episode.mp4")
    run([sys.executable, "-m", "motion_comic", "render", os.path.join(work, "output", "render_timeline.json"),
         "-o", out, "--root", work])
    print("成片:", out)


if __name__ == "__main__":
    main()
