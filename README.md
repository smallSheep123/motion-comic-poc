# motion-comic-poc

视觉导演 → 时间轴编译 → 程序化渲染的动态漫视频引擎。
PoC 阶段已验证视觉定位精度（bbox 偏差 2~5% 画布宽）；v0.4 加入非破坏式页级音频同步工作台。

## 架构

```
漫画页 + 解说词
   │  (视觉模型只做选择题：focus/motion/transition，坐标一律 0~1 归一化)
   ▼
director.json ──────────► narration_manifest.json ──► 任意 TTS 引擎补齐 wav
   │                                                      │
   │            Timeline Compiler（确定性规则）            │
   │            · ffprobe 实测语音时长 → 镜头时长          │
   │            · safe_margin / 边界钳制 / 9:16 比例锁定   │
   │            · min/max 变焦，防人脸出屏、气泡被裁        │
   ▼                                                      ▼
render_timeline.json ◄─────────────────────────────────────┘
   │  同时派生: subtitles.srt / dubbing_sheet.md
   ▼
Renderer（子像素仿射裁剪 + 2x 超采样 + LANCZOS 下采样，pipe → ffmpeg）
   │  音轨按时间戳自动铺位（adelay + amix）
   ▼
episode.mp4（可选再进剪映精修：pyJianYingDraft 草稿导出）
```

核心原则：**AI 负责艺术判断，Compiler 负责规则，Renderer 负责数学**，三层互不渗透。

## 快速开始

```bash
python gen_pages.py                                # 生成测试漫画页（含坐标真值）
python -m motion_comic manifest examples/director.json -o output   # ① 配音清单
python examples/make_demo_audio.py                 # ② TTS 步骤（演示：Windows SAPI 中文音色）
python -m motion_comic compile examples/director.json -o output    # ③ 编译时间轴+字幕
python -m motion_comic render output/render_timeline.json -o output/episode.mp4  # ④ 渲染
python -m unittest discover -s tests               # 回归测试
```

## 配音工作流（为什么是"分镜级音频"）

引擎与 TTS 完全解耦，约定只有一个：**每镜一个 wav，路径写在 manifest 里**。

1. `manifest` 产出每镜的文本/音色/目标 wav 路径；
2. 任意引擎（edge-tts / 智谱 / 火山 / 人声录音）把 wav 补齐——可按镜重配、可多音色混用；
3. `compile` 用 ffprobe **实测**每个 wav 时长（+0.2s 头 / +0.4s 尾）得到镜头时长，
   时间轴、字幕、混音零对齐误差；
4. 导出物：`subtitles.srt`（分句级时间戳，按字数比例分配实测时长，剪映可直接导入）、
   `dubbing_sheet.md`（人工配音/审听时间表）、`render_timeline.json`（全部时间戳的机读源）。

整段录音后想切回分镜？后续可用 whisper 强制对齐，列为 roadmap，不进 v1。

## 阶段②：画面 / 配音同步工作台

完成切图和 TTS 后启动：

```bash
python gui/timeline_editor.py --work real_manga2
```

新版工作台在同一条成片时间尺上显示两条相互独立的轨道：

- **配音轨**：蓝色块两侧分别裁源音频入点 / 出点，拖动整块可移动该段及后续配音；
  配音片段之间的紫色斜纹是真正的无声留白；
- **画面轨**：每个切分 bbox 对应一个连续画面块，拖动橙色分界即可单独改变前后画面的持续时间，
  不要求与配音切分点一一对齐；
- **任意细分**：播放头放进配音块后可新增切分点，因此可以出现“4 段配音 / 3 张画面”；
- **整页预览**：预览始终保留漫画原页，所有 bbox 以蓝框显示，当前输出画面随播放头切换为橙框；
- **留白保持画面**：在配音段后插入留白时，当前画面默认延长，不会自动跳到下一张；
- **非破坏式保存**：原始 IndexTTS 音频不会被裁切覆盖。编辑决定写入
  `timeline_alignment.json`，并同步更新 `director.json` 的编译字段；
- **兼容旧产物**：若现有流程仍是“按页生成、静音切成多个 shot wav”，编辑器会把同页 wav
  在浏览器中拼成连续波形；保存时生成一个独立页级工作副本。

当前版本把画面切换统一保存为 `CUT`，界面暂不暴露转场效果，先把音画节奏与留白编辑做稳定。

时间映射字段如下：

```json
{
  "audio_clips": [
    {"id": "audio_1", "source_start": 0.0, "source_end": 3.42, "timeline_start": 0.0},
    {"id": "audio_2", "source_start": 3.42, "source_end": 6.88, "timeline_start": 3.92}
  ],
  "visual_clips": [
    {"shot_id": "shot_00_01", "timeline_start": 0.0, "timeline_end": 4.60},
    {"shot_id": "shot_00_02", "timeline_start": 4.60, "timeline_end": 7.38}
  ]
}
```

其中第二段配音从成片 `3.92s` 开始，因此前面自动形成 `0.50s` 留白；第一张画面则独立保持到
`4.60s`。`source_start/source_end` 只引用页级原始配音，不改写源文件。

## 目录

```
motion_comic/          引擎包：schema(词表校验) / compiler(相机求解+时间轴)
                      / renderer(渲染) / subtitles(字幕配音导出) / audio / cli
renderers/reference_pil.py   冻结的 PoC 渲染器（对照调试用）
examples/              director.json 示例 + 演示配音脚本
tests/                 compiler / 音画时间轴回归测试（25 例，纯逻辑不碰 ffmpeg）
gen_pages.py           测试页生成器 + ground_truth.json（视觉精度基准）
output/episode.mp4     演示成片（3 镜：缓推特写→长条下扫→拉远收尾，真人声解说）
```

## 抗抖动（评审 P4）

两级措施：①子像素仿射裁剪（`Image.transform` 浮点取样，消除整数取整整微抖）；
②2 倍超采样 + LANCZOS 下采样（抑制网点 moiré 与细线 shimmer）。
`supersample: 1` 可关闭换速度。

## Roadmap

- [x] P0 N 镜头 + 每镜转场（CUT/CROSSFADE/FADE_BLACK/FADE_WHITE）
- [x] P1 归一化坐标（director 层与分辨率解耦）
- [x] P2 Camera Compiler（safe_margin / clamp / 变焦上下限）
- [x] P3 实测 TTS 时长驱动时间轴 + SRT/配音表导出
- [x] P4 子像素 + 超采样渲染
- [ ] P5 十页真实漫画 benchmark（含网点页、右开本、无框分镜等边角案例）
- [ ] 视觉 API 批量页面分析 → director.json 自动生成
- [ ] pyJianYingDraft 剪映草稿导出（optional exporter）
- [ ] 并行渲染 / BGM ducking / loudnorm / 多尺寸导出

## License

MIT
