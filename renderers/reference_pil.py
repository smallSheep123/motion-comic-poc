# -*- coding: utf-8 -*-
"""PoC 渲染器：timeline.json -> mp4。
每帧由"虚拟相机"决定：在页面坐标系里取一个 9:16 的 crop 矩形，
起止矩形之间按 easeInOutCubic 插值；转场区两镜头同时渲染做 alpha blend；
帧序列 pipe 给 ffmpeg 编码。
"""
import json
import os
import subprocess

import numpy as np
from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
OUT_W, OUT_H, FPS = 1080, 1920, 30
ASPECT = OUT_W / OUT_H  # 0.5625


def ease_in_out_cubic(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


class Camera:
    """在页面图上按 (start_rect -> end_rect) 缓动取窗，rect=[x0,y0,x1,y1] 像素。"""

    def __init__(self, img: Image.Image, start, end):
        self.img, self.start, self.end = img, start, end

    def frame(self, t: float) -> np.ndarray:
        e = ease_in_out_cubic(t)
        r = [s + (v - s) * e for s, v in zip(self.start, self.end)]
        r[3] = r[1] + (r[2] - r[0]) / ASPECT  # 锁定输出比例，防漂移
        crop = self.img.crop((int(r[0]), int(r[1]), int(r[2]), int(r[3])))
        return np.asarray(crop.resize((OUT_W, OUT_H), Image.BILINEAR))


def build_camera(page_path: str, shot: dict) -> Camera:
    img = Image.open(page_path).convert("RGB")
    sc = shot.get("camera_scale", 1.0)  # 预放大裁掉纸边、让全景铺满竖屏
    if sc != 1.0:
        img = img.resize((int(img.width * sc), int(img.height * sc)), Image.LANCZOS)
    s = [v * sc for v in shot["start_rect"]]
    e = [v * sc for v in shot["end_rect"]]
    return Camera(img, s, e)


def render(timeline_file: str, audio_wav: str | None, out_mp4: str):
    tl = json.load(open(timeline_file, encoding="utf-8"))
    shots, xf = tl["shots"], tl.get("crossfade_sec", 0.8)
    cams = [build_camera(os.path.join(BASE, sh["page"]), sh) for sh in shots]
    durs = [sh["duration"] for sh in shots]
    assert len(durs) == 2, "PoC 只实现两镜一转场"

    total_dur = durs[0] + durs[1] - xf
    total_frames = int(round(total_dur * FPS))
    print(f"shots={len(shots)} total={total_dur:.2f}s frames={total_frames}", flush=True)

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{OUT_W}x{OUT_H}", "-r", str(FPS), "-i", "-"]
    if audio_wav:
        cmd += ["-i", audio_wav]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
    if audio_wav:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd += [out_mp4]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def shot_frame(i: int, local_t: float) -> np.ndarray:
        return cams[i].frame(min(max(local_t / durs[i], 0.0), 1.0))

    for n in range(total_frames):
        ts = n / FPS
        cut = durs[0] - xf  # 交叉区起点
        if ts < cut:
            frame = shot_frame(0, ts)
        elif ts < durs[0]:
            f_t = (ts - cut) / xf
            frame = (shot_frame(0, ts).astype(np.float32) * (1 - f_t)
                     + shot_frame(1, ts - cut).astype(np.float32) * f_t).astype(np.uint8)
        else:
            frame = shot_frame(1, ts - cut)
        proc.stdin.write(frame.tobytes())
        if n % (FPS * 2) == 0:
            print(f"  {ts:5.1f}s / {total_dur:.1f}s", flush=True)

    proc.stdin.close()
    proc.wait()
    print("done:", out_mp4, "exit=", proc.returncode)


if __name__ == "__main__":
    wav = os.path.join(BASE, "audio", "beep.wav")
    render(
        os.path.join(BASE, "timeline.json"),
        wav if os.path.exists(wav) else None,
        os.path.join(BASE, "output", "poc.mp4"),
    )
