# -*- coding: utf-8 -*-
"""IndexTTS-2.5 单条测试：克隆质量 + duration_factor 语速控制验证。"""
import argparse
import os
import sys
import time

import soundfile as sf
import torch
import torchaudio

# torchaudio 2.10 强制走 torchcodec（本机 DLL 不可用）：用 soundfile 完全替换
def _load(fp, *a, **k):
    data, sr = sf.read(fp, dtype="float32", always_2d=True)
    return torch.from_numpy(data.T.copy()), sr


def _save(uri, src, sample_rate, *a, **k):
    if isinstance(src, torch.Tensor):
        src = src.detach().cpu().numpy()
    if src.ndim == 2:
        src = src.T
    sf.write(uri, src.squeeze(), sample_rate)


torchaudio.load = _load
torchaudio.save = _save

sys.path.insert(0, r"E:\AITTS\index-tts")
from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

REF = r"E:\GPT-SoVITS-v4-20250529\MODEL\parrots-maimai\MaiMai\ref.wav"
CKPT = r"E:\AITTS\index-tts\checkpoints"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="故事开始于一场恋爱吐槽大会，谁能想到，接下来会发生什么呢。")
    ap.add_argument("--out", default=r"E:\AITTS\test_out")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    t0 = time.time()
    tts = IndexTTS2(cfg_path=os.path.join(CKPT, "config.yaml"), model_dir=CKPT, use_bf16=True)
    print(f"model loaded in {time.time()-t0:.0f}s", flush=True)

    for name, factor in [("natural", 1.0), ("slow15", 1.5)]:
        t0 = time.time()
        out = os.path.join(args.out, f"indextts_{name}.wav")
        tts.infer(spk_audio_prompt=REF, text=args.text, lang="ZH",
                  output_path=out, duration_factor=factor, verbose=False)
        info = sf.info(out)
        print(f"{name} (factor={factor}): {info.duration:.2f}s, 推理 {time.time()-t0:.1f}s -> {out}", flush=True)


if __name__ == "__main__":
    main()
