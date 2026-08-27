# -*- coding: utf-8 -*-
"""生成两张测试漫画页 + ground_truth.json（坐标真值），用于评估视觉定位精度。"""
import json
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "pages")
os.makedirs(OUT, exist_ok=True)

W, H = 1200, 1800
BG = (245, 245, 242)

def font(sz):
    for p in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\arial.ttf"]:
        try:
            return ImageFont.truetype(p, sz)
        except OSError:
            continue
    return ImageFont.load_default()

def bubble(d, cx, cy, rw, rh, text, fsz=34):
    """画椭圆对话气泡，返回精确 bbox（尾巴朝左下）。"""
    x0, y0, x1, y1 = cx - rw, cy - rh, cx + rw, cy + rh
    d.polygon([(x0 + 40, y1), (x0 - 25, y1 + 70), (x0 + 110, y1)], fill=(255, 255, 255), outline=(20, 20, 20))
    d.ellipse([x0, y0, x1, y1], fill=(255, 255, 255), outline=(20, 20, 20), width=5)
    f = font(fsz)
    d.text((cx, cy), text, fill=(20, 20, 20), font=f, anchor="mm")
    return [x0, y0, x1, y1]

gt = {"page1": {}, "page2": {}}

# ---------- 页1：四格分镜 ----------
img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

# 格1：远景城市 + 气泡"那条龙来了！"
p1 = (60, 60, 1140, 560)
d.rectangle(p1, fill=(70, 80, 110), outline=(15, 15, 15), width=8)
for i in range(6):  # 楼群剪影
    bx = 100 + i * 170
    d.rectangle([bx, 300 + (i % 3) * 60, bx + 120, 550], fill=(45, 50, 75))
b1 = bubble(d, 850, 165, 230, 62, "那条龙来了！", 36)
gt["page1"]["bubble_1"] = b1
gt["page1"]["panel_1"] = list(p1)

# 格2：中景人物背影 + 气泡"大家快跑"
p2 = (60, 620, 565, 1180)
d.rectangle(p2, fill=(150, 140, 125), outline=(15, 15, 15), width=8)
d.ellipse([250, 700, 380, 830], fill=(60, 45, 40))          # 头
d.polygon([(315, 820), (190, 1170), (440, 1170)], fill=(85, 60, 55))  # 身体
b2 = bubble(d, 220, 700, 135, 52, "快跑！", 32)
gt["page1"]["bubble_2"] = b2
gt["page1"]["panel_2"] = list(p2)

# 格3：特写——龙头（本页情绪高点，焦点应落在这里）
p3 = (635, 620, 1140, 1180)
d.rectangle(p3, fill=(200, 90, 60), outline=(15, 15, 15), width=8)
d.ellipse([720, 700, 1060, 1040], fill=(120, 140, 60))       # 龙头
d.polygon([(820, 760), (760, 620), (900, 730)], fill=(95, 115, 45))   # 角
d.polygon([(960, 750), (1030, 640), (1050, 760)], fill=(95, 115, 45))
d.ellipse([800, 820, 870, 890], fill=(240, 200, 40))          # 眼
d.ellipse([950, 820, 1020, 890], fill=(240, 200, 40))
d.ellipse([818, 838, 850, 872], fill=(10, 10, 10))
d.ellipse([968, 838, 1000, 872], fill=(10, 10, 10))
d.polygon([(880, 940), (910, 985), (940, 940)], fill=(240, 235, 225))  # 鼻吻
for i in range(5):
    d.line([(700 + i * 80, 1120), (740 + i * 80, 1175)], fill=(255, 240, 200), width=6)  # 火焰感线条
gt["page1"]["panel_3"] = list(p3)
gt["page1"]["focus_dragon_head"] = [718, 618, 1162, 1078]  # 特写主体建议框（略扩）

# 格4：人群奔逃 + 气泡"来不及了……"
p4 = (60, 1240, 1140, 1740)
d.rectangle(p4, fill=(120, 120, 130), outline=(15, 15, 15), width=8)
for i in range(4):  # 奔逃小人
    px = 160 + i * 260
    d.ellipse([px, 1380 + (i % 2) * 60, px + 56, 1436 + (i % 2) * 60], fill=(50, 40, 45))
    d.line([(px + 28, 1440 + (i % 2) * 60), (px + 28, 1600 + (i % 2) * 60)], fill=(50, 40, 45), width=10)
b3 = bubble(d, 930, 1330, 175, 55, "来不及了…", 30)
gt["page1"]["bubble_3"] = b3
gt["page1"]["panel_4"] = list(p4)

img.save(os.path.join(OUT, "page1.png"))

# ---------- 页2：长条漫三段（测 pan_down 扫动）----------
img2 = Image.new("RGB", (W, H), BG)
d2 = ImageDraw.Draw(img2)

segA = (60, 60, 1140, 590)
d2.rectangle(segA, fill=(210, 190, 160), outline=(15, 15, 15), width=8)
d2.rectangle([500, 300, 740, 580], fill=(110, 85, 70))       # 门
d2.text((600, 200), "深夜的小镇", fill=(60, 50, 45), font=font(44), anchor="mm")
bbA = bubble(d2, 270, 160, 175, 58, "谁在那？", 32)
gt["page2"]["segment_A"] = list(segA)
gt["page2"]["bubble_A"] = bbA

segB = (60, 650, 1140, 1180)
d2.rectangle(segB, fill=(90, 95, 120), outline=(15, 15, 15), width=8)
d2.ellipse([520, 760, 700, 940], fill=(230, 225, 215))       # 月下白衣人
d2.rectangle([555, 930, 665, 1170], fill=(200, 198, 192))
bbB = bubble(d2, 880, 800, 190, 58, "是你啊。", 32)
gt["page2"]["segment_B"] = list(segB)
gt["page2"]["bubble_B"] = bbB

segC = (60, 1240, 1140, 1740)
d2.rectangle(segC, fill=(40, 42, 55), outline=(15, 15, 15), width=8)
d2.ellipse([420, 1300, 800, 1680], fill=(250, 210, 80))      # 满月
d2.polygon([(430, 1680), (610, 1380), (790, 1680)], fill=(25, 26, 38))  # 屋顶剪影
d2.text((600, 1560), "一切才刚刚开始", fill=(230, 225, 215), font=font(48), anchor="mm")
gt["page2"]["segment_C"] = list(segC)
img2.save(os.path.join(OUT, "page2.png"))

with open(os.path.join(os.path.dirname(__file__), "ground_truth.json"), "w", encoding="utf-8") as fp:
    json.dump(gt, fp, ensure_ascii=False, indent=2)
print("pages generated:", OUT)
