# -*- coding: utf-8 -*-
"""演示用"TTS 步骤"：根据 examples/director.json 的 narration 合成每镜 wav。

这一步在生产中可以换成任意引擎（edge-tts / 智谱 TTS / 火山 / 讯飞 / 人声录音），
引擎只需要遵守同一个契约：按 narration_manifest.json 把每个 wav 写到指定路径。
这里优先尝试 Windows 自带 SAPI 的中文音色；没有中文音色时退化为软提示音
（时长按字数估计），保证流程任何时候都能完整跑通。
"""
import json
import math
import os
import struct
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sapi_voice_names() -> list[str]:
    cmd = ("Add-Type -AssemblyName System.Speech;"
           "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
           "($s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }) -join '|'")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=30)
        return [v for v in p.stdout.strip().split("|") if v]
    except Exception:
        return []


def sapi_speak(text: str, wav_path: str, voice: str) -> bool:
    safe = text.replace("'", "''")
    cmd = ("Add-Type -AssemblyName System.Speech;"
           f"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
           f"$s.SelectVoice('{voice}');$s.Rate=1;"
           f"$s.SetOutputToWaveFile('{wav_path}');$s.Speak('{safe}');$s.Dispose()")
    p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, timeout=120)
    return p.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 4000


def edge_speak(text: str, path: str, voice: str = "zh-CN-YunxiNeural") -> bool:
    """edge-tts（免费、自然音色）。输出 mp3；时长测量与混音链路都经 ffmpeg，格式无关。"""
    try:
        import asyncio
        import edge_tts

        async def run():
            await edge_tts.Communicate(text, voice).save(path)
        asyncio.run(run())
        return os.path.exists(path) and os.path.getsize(path) > 2000
    except Exception as e:
        print(f"    edge-tts failed: {e}")
        return False


def tone_wav(path: str, dur: float, freq: float) -> None:
    sr = 44100
    n = int(sr * dur)
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / (0.2 * sr), (n - i) / (0.3 * sr))
        v = int(0.12 * env * math.sin(2 * math.pi * freq * i / sr) * 32767)
        frames += struct.pack("<h", v)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def main() -> None:
    director_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "examples", "director.json")
    director = json.load(open(director_path, encoding="utf-8"))
    os.makedirs(os.path.join(ROOT, "audio"), exist_ok=True)

    voices = sapi_voice_names()
    zh = [v for v in voices if "Chinese" in v or "中文" in v or "Huihui" in v or "Yaoyao" in v]
    use_voice = zh[0] if zh else None
    engine = f"SAPI {use_voice}" if use_voice else "tone-fallback"

    print(f"TTS engine: {engine}")
    freqs = [220.0, 294.0, 247.0]
    for i, sh in enumerate(director["shots"]):
        n = sh.get("narration") or {}
        if not n.get("text"):
            continue
        out = os.path.join(ROOT, n.get("audio") or f"audio/shot_{i+1:03d}.wav")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        voice = n.get("voice", "narrator")
        ev = "zh-CN-XiaoxiaoNeural" if voice == "female" else "zh-CN-YunxiNeural"
        done = edge_speak(n["text"], out, ev)
        engine_note = f"edge-tts:{ev}"
        if not done:
            done = sapi_speak(n["text"], os.path.splitext(out)[0] + ".wav", use_voice) if use_voice else False
            if done:
                out = os.path.splitext(out)[0] + ".wav"
                engine_note = f"SAPI {use_voice}"
        if not done:
            dur = min(30.0, max(2.0, len(n["text"]) * 0.26 + 0.8))
            tone_wav(out, dur, freqs[i % len(freqs)])
            engine_note = "tone-fallback"
        print(f"  {os.path.relpath(out, ROOT)}  [{engine_note}]  {n['text'][:16]}...")
    print("done.")


if __name__ == "__main__":
    sys.exit(main())
