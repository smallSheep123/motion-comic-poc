# -*- coding: utf-8 -*-
"""F5-TTS 批量驱动 v3：恢复自然生成。

历史教训：v2 的"强制时长重生成"（fix_duration 拉长生成）会让输出出现拖沓/含混伪影，
用户听感"说话不对劲"。v3 移除一切强制：模型自然生成 + 页级整段合成 + 静音切分，
偶发的偏快句改用 atempo 时间拉伸放慢（变速不变调，对 ±20% 无音质损伤）。

运行：E:\\AITTS\\f5-tts-env\\Scripts\\python.exe f5_batch.py --jobs jobs.json [--device cuda]
"""
import argparse
import json
import subprocess
import sys

import numpy as np
import soundfile as sf_lib
import torch  # noqa: E402
import torchaudio  # noqa: E402

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

from f5_tts.api import F5TTS  # noqa: E402


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
    min_gap = int(sr * 0.16)
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


def atempo_slow(pcm: np.ndarray, sr: int, factor: float, out: str) -> None:
    """时间拉伸放慢（变速不变调）。factor<1 放慢。经 ffmpeg atempo。"""
    factor = max(min(factor, 1.0), 0.75)
    tmp = out + ".tmp.wav"
    sf_lib.write(tmp, pcm, sr)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
                    "-filter:a", f"atempo={factor:.3f}", out], check=True)
    import os
    os.remove(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--model", default="F5TTS_v1_Base")
    args = ap.parse_args()

    jobs = json.load(open(args.jobs, encoding="utf-8"))
    f5 = F5TTS(model=args.model, device=args.device)
    print(f"model loaded on {args.device}, {len(jobs)} jobs", flush=True)

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
        wav, sr, _ = f5.infer(
            ref_file=grp[0]["ref_audio"], ref_text=grp[0]["ref_text"],
            gen_text=text, remove_silence=False, cross_fade_duration=0.15,
        )
        pcm = trim(np.asarray(wav).squeeze().astype(np.float32), sr)
        parts = split_by_silence(pcm, sr, len(grp))
        note = ""
        if parts is None:
            parts = []
            for x in grp:
                w1, s1, _ = f5.infer(
                    ref_file=x["ref_audio"], ref_text=x["ref_text"],
                    gen_text=x["text"], remove_silence=False, cross_fade_duration=0.15,
                )
                parts.append(trim(np.asarray(w1).squeeze().astype(np.float32), s1))
            note = " [静音切分失败→逐句回退]"
        for x, p in zip(grp, parts):
            nchars = max(len([c for c in x["text"] if c not in PUNCT]), 1)
            dur = len(p) / sr
            if dur < nchars * 0.18:  # 仅修正真正赶的句子（<0.18s/字），其余保持自然
                factor = max(dur / (nchars * 0.22 + 0.3), 0.75)
                atempo_slow(p, sr, factor, x["out"])
                note += f" | {x['out'].split(chr(92))[-1]} 拉伸x{factor:.2f}"
            else:
                _save(x["out"], p, sr)
        print(f"group {gi}: {len(grp)} 句{note or ' [自然生成]'}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
