# -*- coding: utf-8 -*-
"""N 镜头渲染器：render_timeline.json -> mp4（含自动铺音轨）。

抗抖动两级措施（对应评审 P4）：
1. 子像素仿射裁剪：PIL Image.transform 以浮点坐标取样，消除 int() 取整的
   "停一帧跳一像素"式微抖；
2. 超采样：先在 SS 倍大画布上取样，再 LANCZOS 缩到输出尺寸，高频网点/细线
   的高频能量被下采样平均掉，抑制 moiré 与 shimmer。
"""
from __future__ import annotations

import json
import os
import subprocess
from collections import OrderedDict

import numpy as np
from PIL import Image

# 页面 LRU：长篇只需"当前镜±1"的页驻留内存，21 页全驻留曾把机器顶到卡死。
_PAGE_LRU: "OrderedDict[str, Image.Image]" = OrderedDict()
_LRU_MAX = 3


def _get_page(path: str) -> Image.Image:
    if path in _PAGE_LRU:
        _PAGE_LRU.move_to_end(path)
        return _PAGE_LRU[path]
    img = Image.open(path).convert("RGB")
    _PAGE_LRU[path] = img
    while len(_PAGE_LRU) > _LRU_MAX:
        _PAGE_LRU.popitem(last=False)
    return img


def ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def crop_subpixel(img: Image.Image, rect, out_size, resample=Image.BICUBIC) -> Image.Image:
    """以浮点矩形裁剪并缩放到 out_size，无整数取整。rect=[x0,y0,x1,y1]。"""
    x0, y0, x1, y1 = rect
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    ow, oh = out_size
    sx, sy = ow / w, oh / h
    # AFFINE 数据 (a,b,c,d,e,f)：input = M @ [out_x, out_y, 1]
    return img.transform(out_size, Image.AFFINE, (1 / sx, 0, x0, 0, 1 / sy, y0),
                         resample=resample)


class ShotCam:
    """单镜头虚拟相机。
    fit=None：cover 模式，start_rect -> end_rect 按 easing 取窗并铺满画面；
    fit="letterbox"：定格模式，静止裁取原比例矩形，等比缩放后居中、黑边补底
    （与参考解说视频一致：格子原样展示，绝不裁切放大）。"""

    def __init__(self, page_path: str, start, end, out_size, supersample: int = 2,
                 fit: str | None = None):
        self.page_path = page_path
        self.start, self.end = start, end
        self.ss = max(1, supersample)
        self.big = (out_size[0] * self.ss, out_size[1] * self.ss)
        self.out_size = out_size
        self.fit = fit

    def frame(self, p: float) -> np.ndarray:
        if self.fit == "letterbox":
            ow, oh = self.out_size
            r = self.start
            rw, rh = max(r[2] - r[0], 1.0), max(r[3] - r[1], 1.0)
            scale = min(ow * 0.96 / rw, oh * 0.96 / rh)
            tw, th = max(int(rw * scale), 2), max(int(rh * scale), 2)
            crop = crop_subpixel(_get_page(self.page_path), r, (tw, th), Image.BICUBIC)
            canvas = Image.new("RGB", (ow, oh), (12, 12, 12))
            canvas.paste(crop, ((ow - tw) // 2, (oh - th) // 2))
            return np.asarray(canvas, dtype=np.uint8)
        e = ease_in_out_cubic(p)
        r = [s + (v - s) * e for s, v in zip(self.start, self.end)]
        fr = crop_subpixel(_get_page(self.page_path), r, self.big, Image.BICUBIC)
        if self.ss > 1:
            fr = fr.resize(self.out_size, Image.LANCZOS)
        return np.asarray(fr, dtype=np.uint8)


def mix_audio_tracks(tracks: list[dict], total_sec: float, out_wav: str) -> str:
    """Python 侧预混音：每条音轨解码成 PCM，按时间戳叠进一条静音轨。
    不用 ffmpeg 的 amix+adelay 滤镜图——多个文件输入叠加无限补齐流时
    交错逻辑会停摆（实测 21 轨卡死），预混后 ffmpeg 只见一条普通音轨。"""
    import wave

    sr = 44100
    buf = np.zeros(int(total_sec * sr) + sr, dtype=np.float32)  # +1s 余量
    for tr in tracks:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", tr["file"], "-f", "s16le",
             "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sr), "-"],
            capture_output=True, check=True)
        x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        i0 = int(tr["start"] * sr)
        buf[i0:i0 + len(x)] += x
    pcm = np.clip(buf, -1.0, 1.0)
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())
    return out_wav


def _blend(a, b, p, transition):
    a, b = a.astype(np.float32), b.astype(np.float32)
    if transition == "FADE_BLACK":
        return (a * (1 - 2 * p) if p < 0.5 else b * (2 * p - 1)).astype(np.uint8)
    if transition == "FADE_WHITE":
        if p < 0.5:
            k = 2 * p
            return (a * (1 - k) + 255.0 * k).astype(np.uint8)
        k = 2 * p - 1
        return (255.0 * (1 - k) + b * k).astype(np.uint8)
    return (a * (1 - p) + b * p).astype(np.uint8)  # CROSSFADE


def render(timeline_path: str, out_mp4: str, root: str = ".") -> dict:
    tl = json.load(open(timeline_path, encoding="utf-8"))
    ow, oh, fps = tl["output"]["width"], tl["output"]["height"], tl["output"]["fps"]
    xf = float(tl.get("crossfade_sec", 0.6))
    ss = int(tl.get("supersample", 2))
    shots = tl["shots"]
    starts = [s["global_start"] for s in shots]
    durs = [s["duration"] for s in shots]
    total = tl["total_duration"]
    n_frames = int(round(total * fps))
    print(f"shots={len(shots)} total={total:.2f}s frames={n_frames} ss=x{ss}", flush=True)

    cams = [ShotCam(os.path.join(root, sh["page"]), sh["start_rect"], sh["end_rect"], (ow, oh), ss,
                    fit=sh.get("fit"))
            for sh in shots]

    def shot_frame(i, t):
        p = (t - starts[i]) / durs[i]
        settle = shots[i].get("settle_at")
        if settle:  # "推到位就停"：运动压缩到 settle_at 前完成，之后画面完全静止
            p = min(p / settle, 1.0)
        return cams[i].frame(p)

    def owner(t):
        i = 0
        for k in range(len(starts)):
            if starts[k] <= t + 1e-9:
                i = k
        return i

    tracks = tl.get("audio_tracks") or []
    mixed_wav = None
    if tracks:
        mixed_wav = os.path.join(os.path.dirname(os.path.abspath(out_mp4)), "_narration_mixed.wav")
        mix_audio_tracks([{"file": os.path.join(root, t["file"]), "start": t["start"]} for t in tracks],
                         float(total), mixed_wav)

    os.makedirs(os.path.dirname(os.path.abspath(out_mp4)), exist_ok=True)
    flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0

    # 两段式：管道阶段只写视频。实测"管道视频 + 文件音频"单段混写时，ffmpeg
    # 的交错逻辑会无界缓存 rawvideo 包（数 GB），两次拖垮整机；拆开后管道段
    # 只有视频流，混音段从文件离线跑，内存都有界。
    stage1 = out_mp4 if not mixed_wav else os.path.join(
        os.path.dirname(os.path.abspath(out_mp4)), "_video_stage1.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ow}x{oh}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "15", "-pix_fmt", "yuv420p", stage1]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=flags)

    for n in range(n_frames):
        t = n / fps
        i = owner(t)
        if i + 1 < len(shots) and t >= starts[i + 1] - 1e-9:
            tr = shots[i].get("transition_out", "CUT")
            ov = 0.0 if tr == "CUT" else xf
            if ov > 0:
                p = max(0.0, min(1.0, (t - starts[i + 1]) / ov))
                frame = _blend(shot_frame(i, t), shot_frame(i + 1, t), p, tr)
            else:
                frame = shot_frame(i, t)
        else:
            frame = shot_frame(i, t)
        proc.stdin.write(frame.tobytes())
        if n % (fps * 2) == 0:
            print(f"  {t:5.1f}s / {total:.1f}s", flush=True)

    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg(stage1 视频管道) 退出码 {proc.returncode}")

    if mixed_wav:
        p2 = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", stage1, "-i", mixed_wav,
             "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-shortest", out_mp4],
            creationflags=flags)
        if p2.returncode != 0:
            raise RuntimeError(f"ffmpeg(stage2 混音) 退出码 {p2.returncode}")
        os.remove(stage1)

    return {"output": out_mp4, "duration": total, "frames": n_frames,
            "audio_tracks": len(tracks)}
