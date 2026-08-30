# -*- coding: utf-8 -*-
"""IndexTTS-2.5 批量驱动 v2：按页整段合成 + 静音切分（方案一）。

演变：
  v1 逐镜独立合成 —— 画面锁得准，但句间韵律断裂（上下文不连贯）。
  v2 按页整段：同一页的句子拼成一段文本，一次推理生成（IndexTTS 原生支持
  长文本与标点停顿），再用能量包络按自然停顿切回各镜。韵律连贯 + 逐镜对齐
  两全。块级语速守门：整段明显偏快时用 duration_factor（生成期控制，无伪影）。
  情绪向量已按产品决策移除（单旁白逐句情绪 = 打碎叙事基调）。

运行：E:\\AITTS\\indextts-env\\Scripts\\python.exe indextts_batch.py --jobs jobs.json
jobs.json: [{"text","out","ref_audio","group"} ...]（按阅读顺序）
"""
import argparse
import json
import os
import sys

import numpy as np
import soundfile as sf_lib
import torch
import torchaudio

PUNCT = "。，！？；：、…—·,.!?;:\"'（）()《》【】 \n"


def _load(fp, *a, **k):
    data, sr = sf_lib.read(fp, dtype="float32", always_2d=True)
    return torch.from_numpy(data.T.copy()), sr


def _save(uri, src, sample_rate, *a, **k):
    if isinstance(src, torch.Tensor):
        src = src.detach().cpu().numpy()
    if src.ndim == 2:
        src = src.T
    sf_lib.write(uri, src.squeeze(), sample_rate)


torchaudio.load = _load
torchaudio.save = _save

sys.path.insert(0, r"E:\AITTS\index-tts")
from indextts.infer_v2_5 import IndexTTS2  # noqa: E402


def envelope(pcm: np.ndarray, sr: int) -> np.ndarray:
    w = max(int(sr * 0.01), 1)
    return np.convolve(np.abs(pcm), np.ones(w) / w, mode="same")


def trim(pcm: np.ndarray, sr: int, head=0.12, tail=0.25) -> np.ndarray:
    env = envelope(pcm, sr)
    th = max(env.max() * 0.03, 1e-4)
    idx = np.where(env > th)[0]
    if len(idx) == 0:
        return pcm
    a = max(int(idx[0] - head * sr), 0)
    b = min(int(idx[-1] + tail * sr), len(pcm))
    return pcm[a:b]


def split_by_silence(pcm: np.ndarray, sr: int, n: int):
    env = envelope(pcm, sr)
    th = max(env.max() * 0.04, 1e-4)
    silent = env < th
    min_gap = int(sr * 0.15)
    gaps, i = [], 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j < len(silent) and silent[j]:
                j += 1
            if j - i >= min_gap and i > sr * 0.2 and j < len(silent) - sr * 0.2:
                gaps.append((i, j))
            i = j
        else:
            i += 1
    if len(gaps) < n - 1:
        return None
    gaps.sort(key=lambda g: -(g[1] - g[0]))
    gaps = sorted(gaps[: n - 1])
    out, prev = [], 0
    for a, b in gaps:
        out.append(pcm[prev:(a + b) // 2])
        prev = (a + b) // 2
    out.append(pcm[prev:])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--ckpt", default=r"E:\AITTS\index-tts\checkpoints")
    args = ap.parse_args()

    jobs = json.load(open(args.jobs, encoding="utf-8"))
    tts = IndexTTS2(cfg_path=rf"{args.ckpt}\config.yaml", model_dir=args.ckpt, use_bf16=True)
    print(f"model loaded, {len(jobs)} jobs", flush=True)

    groups, cur, cur_key = [], [], object()
    for j in jobs:
        key = j.get("group", f"solo_{id(j)}")
        if cur and key != cur_key:
            groups.append(cur)
            cur = []
        cur.append(j)
        cur_key = key
    if cur:
        groups.append(cur)

    for gi, grp in enumerate(groups, 1):
        text = "".join(x["text"] for x in grp)
        nchars = max(len([c for c in text if c not in PUNCT]), 1)
        block = os.path.join(os.path.dirname(grp[0]["out"]), "_block.wav")
        tts.infer(
            spk_audio_prompt=grp[0]["ref_audio"], text=text, lang="ZH",
            output_path=block, verbose=False,
        )
        data, sr = _load(block)
        pcm = trim(data.mean(dim=0).numpy().astype(np.float32), sr)
        # 块级语速守门（宽松：只救严重偏快，生成期 duration_factor 无伪影）
        dur = len(pcm) / sr
        if dur < nchars * 0.17:
            factor = min((nchars * 0.22 + 0.3) / dur, 1.6)
            tts.infer(
                spk_audio_prompt=grp[0]["ref_audio"], text=text, lang="ZH",
                output_path=block, verbose=False, duration_factor=factor,
            )
            data, sr = _load(block)
            pcm = trim(data.mean(dim=0).numpy().astype(np.float32), sr)
            dur = len(pcm) / sr
            print(f"group {gi}: 重生成 factor={factor:.2f}", flush=True)
        parts = split_by_silence(pcm, sr, len(grp))
        note = ""
        if parts is None:
            for x in grp:
                tts.infer(
                    spk_audio_prompt=x["ref_audio"], text=x["text"], lang="ZH",
                    output_path=x["out"], verbose=False,
                )
                data, s1 = _load(x["out"])
                _save(x["out"], trim(data.mean(dim=0).numpy().astype(np.float32), s1), s1)
            note = " [静音切分失败→逐句回退]"
        else:
            for x, p in zip(grp, parts):
                _save(x["out"], p, sr)
        print(f"group {gi}: {len(grp)} 句, 整段 {dur:.1f}s{note}", flush=True)


if __name__ == "__main__":
    main()
