# -*- coding: utf-8 -*-
"""墨线吸附：把视觉模型估计的格子粗框对齐到真实格线。

对粗框的每条边，在 ±SEARCH 范围内沿边取中段条带，统计每行/列的暗像素占比，
取峰值处为新边界；若峰值占比低于 LINE_MIN（该处不是长直黑线，如出血格）则保留原值。
输出 real_manga/panels_vlm.json（吸附后）。
"""
import json
import os

import cv2
import numpy as np

import sys
PAGES = sys.argv[1] if len(sys.argv) > 1 else "real_manga/pages"
RAW = sys.argv[2] if len(sys.argv) > 2 else "real_manga/panels_vlm_raw.json"
OUT = sys.argv[3] if len(sys.argv) > 3 else "real_manga/panels_vlm.json"
SEARCH_FRAC = 0.045   # 搜索半径（相对页长边）
MID_FRAC = 0.6        # 每条边只取中段统计，避免角部干扰
LINE_MIN = 0.55       # 认定为格线的暗像素占比阈值


def snap(page_gray: np.ndarray, box):
    gray = page_gray
    H, W = gray.shape
    search = int(max(H, W) * SEARCH_FRAC)
    x0, y0, x1, y1 = [int(round(v * (W if i % 2 == 0 else H))) for i, v in enumerate(box)]
    dark = (gray < 120).astype(np.float32)
    out = [float(x0), float(y0), float(x1), float(y1)]
    span = (x1 - x0)
    a, b = x0 + int(span * (1 - MID_FRAC) / 2), x1 - int(span * (1 - MID_FRAC) / 2)

    def best_line(profile, center):  # profile: 暗度序列, center: 当前边界
        lo, hi = max(center - search, 0), min(center + search, len(profile) - 1)
        if hi <= lo:
            return center, 0.0
        seg = profile[lo:hi + 1]
        k = int(np.argmax(seg))
        return lo + k, float(seg[k])

    # 上边：在 y∈[y0-search, y0+search] 找暗行（统计 x 中段）
    p = dark[:, a:b].mean(axis=1)
    ny, s = best_line(p, y0)
    if s >= LINE_MIN: out[1] = float(ny)
    # 下边
    p = dark[:, a:b].mean(axis=1)
    ny, s = best_line(p, y1)
    if s >= LINE_MIN: out[3] = float(ny)
    # 左边 / 右边：统计 y 中段
    c, d = y0 + int((y1 - y0) * (1 - MID_FRAC) / 2), y1 - int((y1 - y0) * (1 - MID_FRAC) / 2)
    p = dark[c:d, :].mean(axis=0)
    nx, s = best_line(p, x0)
    if s >= LINE_MIN: out[0] = float(nx)
    p = dark[c:d, :].mean(axis=0)
    nx, s = best_line(p, x1)
    if s >= LINE_MIN: out[2] = float(nx)
    # 交叉保护：确保顺序
    if out[2] - out[0] < 20 or out[3] - out[1] < 20:
        return [float(x0), float(y0), float(x1), float(y1)], False
    moved = any(abs(out[i] - v) > 3 for i, v in enumerate([x0, y0, x1, y1]))
    return [round(v / (W if i % 2 == 0 else H), 4) for i, v in enumerate(out)], moved


def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    out = {}
    for page, boxes in raw.items():
        gray = cv2.cvtColor(cv2.imread(os.path.join(PAGES, page)), cv2.COLOR_BGR2GRAY)
        snapped = []
        for b in boxes:
            nb, moved = snap(gray, b)
            snapped.append(nb)
            print(f"{page} {b} -> {nb} {'snapped' if moved else 'kept'}")
        out[page] = snapped
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
