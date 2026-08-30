# -*- coding: utf-8 -*-
"""音色海选：同一句话 × 多个参考音频，生成试听包。"""
import os
import sys

import soundfile as sf
import torch
import torchaudio


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

VOICES = r"E:\AITTS\voices"
OUT = r"E:\AITTS\test_out"
TEXT = "故事开始于一场恋爱吐槽大会。谁能想到，接下来会发生什么呢？"

tts = IndexTTS2(cfg_path=r"E:\AITTS\index-tts\checkpoints\config.yaml",
                model_dir=r"E:\AITTS\index-tts\checkpoints", use_bf16=True)
print("model loaded", flush=True)

for name in sorted(os.listdir(VOICES)):
    stem = os.path.splitext(name)[0]
    out = os.path.join(OUT, f"aud_{stem}.wav")
    try:
        tts.infer(spk_audio_prompt=os.path.join(VOICES, name), text=TEXT, lang="ZH",
                  output_path=out, verbose=False)
        print(f"ok  {stem} -> {out}", flush=True)
    except Exception as e:
        print(f"FAIL {stem}: {e}", flush=True)
