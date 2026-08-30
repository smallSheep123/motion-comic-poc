# -*- coding: utf-8 -*-
"""Kokoro 中文音色批量采样：同一段文本用多个预置音色合成，供挑选。"""
import argparse
import json
import os
import sys
import time

import soundfile as sf

from kokoro import KPipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="故事开始于一场恋爱吐槽大会。谁能想到，接下来会发生什么呢？")
    ap.add_argument("--voices", default="zf_xiaobei,zf_xiaoni,zf_xiaoxiao,zf_xiaoyi")
    ap.add_argument("--out", default=r"E:\AITTS\test_out")
    ap.add_argument("--jobs", help="批量模式：[{text,out,voice}] 列表的 json 文件")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    t0 = time.time()
    pipe = KPipeline(lang_code="z", repo_id="hexgrad/Kokoro-82M-v1.1-zh")
    print(f"model ready in {time.time()-t0:.0f}s", flush=True)

    import numpy as np
    if args.jobs:
        jobs = json.load(open(args.jobs, encoding="utf-8"))
        for i, j in enumerate(jobs, 1):
            chunks = [audio for _gs, _ps, audio in pipe(j["text"], voice=j.get("voice", "zf_001"), speed=1.0)]
            full = np.concatenate(chunks)
            sf.write(j["out"], full, 24000)
            print(f"[{i}/{len(jobs)}] {j['out']} ({len(full)/24000:.2f}s)", flush=True)
        return

    for v in args.voices.split(","):
        v = v.strip()
        if not v:
            continue
        t0 = time.time()
        chunks = [audio for _gs, _ps, audio in pipe(args.text, voice=v, speed=1.0)]
        full = np.concatenate(chunks)
        path = os.path.join(args.out, f"kokoro_{v}.wav")
        sf.write(path, full, 24000)
        print(f"{v}: {len(full)/24000:.2f}s (推理 {time.time()-t0:.1f}s) -> {path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
