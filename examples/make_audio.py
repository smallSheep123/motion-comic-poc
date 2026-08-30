# -*- coding: utf-8 -*-
"""统一配音步骤（替代 make_demo_audio.py）。

两种引擎：
  edge    —— edge-tts 神经音色。默认整集"连续合成一次"，利用 WordBoundary 字级
            时间戳把长音频精确切回每个镜头——上下文韵律连贯，解决逐段合成
            "每段重新开机"的不连贯问题；对齐失败自动退回逐镜合成。
  sovits  —— 本地 GPT-SoVITS（需先手动启动 api_v2.py 服务），零样本/定制音色，
            逐镜合成（同一参考音色，音色一致性好）。

用法：
  python examples/make_audio.py <director.json> [--engine edge] [--voice zh-CN-YunxiNeural]
  python examples/make_audio.py <director.json> --engine sovits \
      [--sovits-api http://127.0.0.1:9880] [--ref-audio .../ref.wav] [--prompt-text ...]
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 44100

MAIMAI_DIR = r"E:\GPT-SoVITS-v4-20250529\MODEL\parrots-maimai\MaiMai"
MAIMAI_PROMPT = ("那我们，唠也唠了这么久了唠了有十几分钟了我们要不来唱唱，"
                 "唱唱歌，想听什么，今天想听什么。")

PUNCT = "。，！？；：、…—·,.!?;:\"'“”‘’（）()《》<>【】[]~-—\n\r "


def strip_punct(s: str) -> str:
    return "".join(c for c in s if c not in PUNCT)


def decode_pcm(path: str) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le",
                        "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SR), "-"],
                       capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def save_wav(path: str, pcm: np.ndarray) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())


def ffprobe_dur(path: str) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", path], capture_output=True, text=True, check=True)
    return float(json.loads(p.stdout)["format"]["duration"])


# ---------------------------------------------------------------- edge 连续合成
def edge_continuous(shots: list[dict], voice: str) -> bool:
    import edge_tts

    texts = [s["narration"]["text"] for s in shots]
    full = "".join(t if t.endswith(("。", "！", "？", "…")) else t + "。" for t in texts)
    audio, events = bytearray(), []
    total_stripped = len(strip_punct(full))

    async def run():
        c = edge_tts.Communicate(full, voice, rate="-4%")
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                events.append((chunk["offset"] / 1e7, chunk["duration"] / 1e7,
                               len(strip_punct(chunk["text"]))))
    try:
        asyncio.run(run())
    except Exception as e:
        print(f"  edge 连续合成失败: {e}，退回逐镜模式")
        return False

    if not audio or not events or sum(e[2] for e in events) < total_stripped * 0.8:
        print("  字级时间戳不完整，退回逐镜模式")
        return False

    tmp_mp3 = os.path.join(ROOT, "output", "_edge_full.mp3")
    os.makedirs(os.path.dirname(tmp_mp3), exist_ok=True)
    with open(tmp_mp3, "wb") as f:
        f.write(bytes(audio))
    full_pcm = decode_pcm(tmp_mp3)
    n = len(full_pcm)

    # 把字事件铺到"骨架字符"轴上，再按每镜骨架长度切时间
    spans, pos = [], 0.0  # events -> [(t_start, t_end)]
    for off, dur, ln in events:
        spans.append((off, off + max(dur, 0.02), pos, pos + ln))
        pos += ln
    scale = n / (spans[-1][1] if spans[-1][1] > 0 else 1.0)

    cpos = 0
    for s, text in zip(shots, texts):
        need = len(strip_punct(text))
        want0, want1 = cpos, cpos + need
        cpos = want1
        t0 = next((a for a, b, c0, c1 in spans if c1 > want0), spans[0][0])
        t1 = next((b for a, b, c0, c1 in reversed(spans) if c0 < want1), spans[-1][1])
        i0, i1 = int(t0 * SR), min(int(t1 * SR) + int(0.25 * SR), n)
        if i1 <= i0:
            return False
        save_wav(s["audio"], full_pcm[i0:i1])
    return True


def edge_per_shot(shots: list[dict], voice: str) -> None:
    import edge_tts

    async def one(text, path):
        await edge_tts.Communicate(text, voice, rate="-4%").save(path + ".tmp.mp3")
    for s in shots:
        tmp = s["audio"] + ".tmp.mp3"
        asyncio.run(one(s["narration"]["text"], tmp))
        pcm = decode_pcm(tmp)
        save_wav(s["audio"], np.concatenate([pcm, np.zeros(int(0.25 * SR))]))
        os.remove(tmp)
        print(f"  [edge/逐镜] {os.path.basename(s['audio'])}")


# ---------------------------------------------------------------- GPT-SoVITS
def sovits_synth(shots: list[dict], api: str, ref: str, prompt_text: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本地服务不走系统代理
    for s in shots:
        q = urllib.parse.urlencode({
            "text": s["narration"]["text"], "text_lang": "zh",
            "ref_audio_path": ref, "prompt_text": prompt_text, "prompt_lang": "zh",
            "text_split_method": "cut5", "batch_size": 1,
            "media_type": "wav", "streaming_mode": False,
        })
        url = f"{api}/tts?{q}"
        try:
            with opener.open(url, timeout=300) as r:
                data = r.read()
            tmp = s["audio"] + ".tmp.wav"
            with open(tmp, "wb") as f:
                f.write(data)
            save_wav(s["audio"], decode_pcm(tmp))  # 统一 44.1k 单声道
            os.remove(tmp)
            print(f"  [sovits] {os.path.basename(s['audio'])}")
        except Exception as e:
            sys.exit(f"  sovits 合成失败 {os.path.basename(s['audio'])}: {e}\n"
                     f"  请确认已启动: runtime\\python.exe api_v2.py -a 127.0.0.1 -p 9880 "
                     f"-c GPT_SoVITS/configs/tts_infer.yaml")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("director")
    ap.add_argument("--engine", choices=["edge", "sovits", "f5", "kokoro", "indextts"], default="edge")
    ap.add_argument("--voice", default="zh-CN-YunxiNeural")
    ap.add_argument("--sovits-api", default="http://127.0.0.1:9880")
    ap.add_argument("--ref-audio", default=os.path.join(MAIMAI_DIR, "ref.wav"))
    ap.add_argument("--prompt-text", default="",
                    help="sovits: 留空=无参考文本模式（推荐）；f5: 留空自动用 MaiMai 参考文字稿")
    ap.add_argument("--f5-python", default=r"E:\AITTS\f5-tts-env\Scripts\python.exe")
    ap.add_argument("--f5-device", default="cuda", choices=["cuda", "cpu"])
    args = ap.parse_args()

    d = json.load(open(args.director, encoding="utf-8"))
    shots = []
    for i, sh in enumerate(d["shots"]):
        n = sh.get("narration") or {}
        if n.get("text"):
            old = n.get("audio") or f"audio/shot_{i+1:03d}.mp3"
            wav = os.path.splitext(old)[0] + ".wav"
            if not os.path.isabs(wav):
                wav = os.path.join(os.path.dirname(os.path.abspath(args.director)), wav)
            n["audio"] = wav
            sh["audio"] = wav
            shots.append(sh)
    with open(args.director, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    print(f"TTS engine: {args.engine}, {len(shots)} 段")
    if args.engine == "edge":
        if not edge_continuous(shots, args.voice):
            edge_per_shot(shots, args.voice)
        else:
            print("  [edge/连续] 整集一次合成并按字级时间戳切片完成")
    elif args.engine == "indextts":
        jobs = [{"text": s["narration"]["text"], "out": s["audio"],
                 "ref_audio": s["narration"].get("voice") or args.ref_audio,
                 "group": s["id"].rsplit("_", 1)[0]}
                for s in shots]
        jp = os.path.join(ROOT, "output", "_indextts_jobs.json")
        os.makedirs(os.path.dirname(jp), exist_ok=True)
        json.dump(jobs, open(jp, "w", encoding="utf-8"), ensure_ascii=False)
        env = dict(os.environ, NO_PROXY="*", no_proxy="*")
        r = subprocess.run([args.f5_python.replace("f5-tts-env", "indextts-env"),
                            os.path.join(ROOT, "examples", "indextts_batch.py"), "--jobs", jp], env=env)
        if r.returncode != 0:
            sys.exit("indextts 批量合成失败，见上方日志")
    elif args.engine == "kokoro":
        jobs = [{"text": s["narration"]["text"], "out": s["audio"],
                 "voice": args.voice if args.voice.startswith(("zf_", "zm_")) else "zf_001"}
                for s in shots]
        jp = os.path.join(ROOT, "output", "_kokoro_jobs.json")
        os.makedirs(os.path.dirname(jp), exist_ok=True)
        json.dump(jobs, open(jp, "w", encoding="utf-8"), ensure_ascii=False)
        env = dict(os.environ, HF_ENDPOINT="https://hf-mirror.com", NO_PROXY="*", no_proxy="*")
        r = subprocess.run([args.f5_python, os.path.join(ROOT, "examples", "kokoro_sample.py"),
                            "--jobs", jp], env=env)
        if r.returncode != 0:
            sys.exit("kokoro 批量合成失败，见上方日志")
    elif args.engine == "f5":
        jobs = [{"text": s["narration"]["text"], "out": s["audio"],
                 "ref_audio": args.ref_audio,
                 "ref_text": args.prompt_text or MAIMAI_PROMPT,
                 "group": s["id"].rsplit("_", 1)[0]} for s in shots]
        jp = os.path.join(ROOT, "output", "_f5_jobs.json")
        os.makedirs(os.path.dirname(jp), exist_ok=True)
        json.dump(jobs, open(jp, "w", encoding="utf-8"), ensure_ascii=False)
        env = dict(os.environ, HF_ENDPOINT="https://hf-mirror.com", NO_PROXY="*", no_proxy="*")
        r = subprocess.run([args.f5_python, os.path.join(ROOT, "examples", "f5_batch.py"),
                            "--jobs", jp, "--device", args.f5_device], env=env)
        if r.returncode != 0:
            sys.exit("f5 批量合成失败，见上方日志")
    else:
        sovits_synth(shots, args.sovits_api, args.ref_audio, args.prompt_text)
    for s in shots:
        assert os.path.exists(s["narration"]["audio"]), s["narration"]["audio"]
    print("done.")


if __name__ == "__main__":
    main()
