# -*- coding: utf-8 -*-
"""漫画格子自动检测：白底黑框 → 阈值化 → 闭运算 → 大矩形轮廓 → 格子 bbox。

输出：
  real_manga/panels.json   每页的格子列表（归一化坐标，日漫阅读序：右→左、上→下）
  real_manga/panels_vis/   可视化核对图（红框标出检出格子）
"""
import json
import os

import cv2
import numpy as np

PAGES = "real_manga/pages"
OUT_JSON = "real_manga/panels.json"
VIS = "real_manga/panels_vis"

MIN_AREA_FRAC = 0.012  # 格子最小面积（占页面比例）
MIN_SIDE_FRAC = 0.10   # 格子最小边长（占对应边比例）


def detect_panels(path: str):
    img = cv2.imread(path)
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 格线是深色：阈值 128 以下为墨
    _, binv = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)
    # 闭运算把格线连成实心矩形（核随页面尺寸缩放，须小于格间距）
    k = max(H, W) // 180
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    closed = cv2.morphologyEx(binv, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area_frac = (w * h) / (W * H)
        if area_frac < MIN_AREA_FRAC:
            continue
        if w / W < MIN_SIDE_FRAC or h / H < MIN_SIDE_FRAC:
            continue
        # 贴满整页的框是页面本身/背景，跳过
        if w / W > 0.97 and h / H > 0.97:
            continue
        boxes.append([x / W, y / H, (x + w) / W, (y + h) / H, area_frac])
    # 过滤互相包含的大框（闭运算粘连）：若 A⊂B 且面积接近，留小的
    boxes.sort(key=lambda b: -b[4])
    kept = []
    for b in boxes:
        contained = False
        for k_ in kept:
            if (b[0] >= k_[0] - 0.01 and b[1] >= k_[1] - 0.01
                    and b[2] <= k_[2] + 0.01 and b[3] <= k_[3] + 0.01):
                contained = True
                break
        if not contained:
            kept.append(b)
    # 日漫阅读顺序：上→下优先，同一行内右→左
    kept.sort(key=lambda b: (round(b[1] / 0.18), -b[0]))
    return [[round(v[0], 4), round(v[1], 4), round(v[2], 4), round(v[3], 4)] for v in kept], img


def main():
    os.makedirs(VIS, exist_ok=True)
    out = {}
    for name in sorted(os.listdir(PAGES)):
        if not name.endswith((".jpg", ".png")):
            continue
        path = os.path.join(PAGES, name)
        boxes, img = detect_panels(path)
        out[name] = boxes
        for i, b in enumerate(boxes):
            x0, y0, x1, y1 = [int(v * (img.shape[1] if j % 2 == 0 else img.shape[0]))
                              for j, v in enumerate(b)]
            cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 255), 12)
            cv2.putText(img, str(i), (x0 + 20, y0 + 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 255), 8)
        vis = cv2.resize(img, (img.shape[1] // 3, img.shape[0] // 3))
        cv2.imwrite(os.path.join(VIS, name), vis)
        print(f"{name}: {len(boxes)} panels")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved:", OUT_JSON)


if __name__ == "__main__":
    main()
