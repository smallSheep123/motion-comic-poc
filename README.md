# 动态漫自动化 PoC 技术验证

结论：**可行**。三个关键环节全部实测通过（2026-08-27）。

## 实测结果

| 环节 | 方法 | 结果 |
|---|---|---|
| 环境 | python 3.14 + ffmpeg 8.0.1 + PIL + numpy | 全部就绪，零新增依赖 |
| 视觉定位 | 视觉模型读页面图 → 气泡/焦点 bbox | 偏差 ≈ 画布宽度的 2~5%，加安全边距后满足运镜需求；气泡文字可读、情绪焦点判断正确 |
| 程序化渲染 | PIL 逐帧(虚拟相机 crop+easing) pipe→ffmpeg | 1080x1920@30fps、h264+aac、时长精确；216 帧渲染 < 10 秒 |
| 转场 | 两镜交叉区 alpha blend (crossfade 0.8s) | 抽帧检查过渡自然，无黑帧/闪烁/花屏 |

## 文件说明

- `gen_pages.py` — 生成测试漫画页 + `ground_truth.json`（已知坐标真值，用于量化视觉精度）
- `timeline.json` — 时间轴示例（这就是视觉模型+编排器最终的输出物）
- `render_poc.py` — 渲染器原型：虚拟相机(crop+easeInOutCubic) + crossfade + 音轨 mux
- `output/poc.mp4` — 成品（shot1 缓推特写 + crossfade + shot2 长条漫下扫 + beep 音轨）
- `qc/frame_*.png` — 抽帧质检演示

## 生产化要点（PoC 未含）

1. TTS 后以真实语音时长驱动每镜 duration（停留 = max(语音+0.4s, 可读时长)）
2. 批量时视觉分析走智谱开放平台 vision API，与本地验证同级能力
3. 多进程按镜头并行渲染，长篇横向扩容
4. 抽帧 QC 闭环：转场前后±0.5s 抽帧回视觉模型检查构图/遮挡
5. 转场模板库统一保底：crossfade 建议 0.4~0.6s，动作衔接可用 whip-pan，白底页面长叠化会显"透"
6. 衔接剪映人工润色：用 pyJianYingDraft 把 timeline 转成剪映草稿(draft_content.json)，剪映打开即见已排好的轨道，人工只做精修

## 复现

```bash
python gen_pages.py
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=7.2" -af volume=0.15 audio/beep.wav
python render_poc.py
```
