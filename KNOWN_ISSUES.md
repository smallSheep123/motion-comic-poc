# KNOWN ISSUES（交接给下一个维护者/AI）

## 1. 时间轴编辑器右侧缩略图与左侧格子框不一致（未解决，待定位）

**现象**：`/timeline` 页面里，左侧大图的 SVG 格子框位置正确（与真实格子吻合），
但右侧时间轴块上的小缩略图显示的区域不对——有的显示成接近整页，有的只剩局部。

**已排除/已修复**：
- 页面尺寸曾硬编码为上一部作品的 2122×2976（当前作品是 1810×2560），
  已改为从 `$("img").naturalWidth/Height` 读取（commit 见 gui/timeline.html）；
- 缩略图容器改为 contain 居中裁切（inner div overflow:hidden）。

**仍待排查的候选原因**（按可能性排序）：
1. **bbox 数据源过期**：左侧框和右侧缩略图都用 `GET /api/data` 返回的
   `focus.bbox`（来自 director.json）。若步骤①的 panels_final.json 在
   director 生成**之后**又被重新调整保存，director 里的 bbox 就是旧的。
   → 检查 real_manga2/panels_final.json 与 director.json 中 bbox 是否一致。
2. **图片加载竞态**：缩略图 inner img 渲染时若图片未缓存完成会显示黑块
   （thumb 有 #111 底色）。可在 inner img 上加 load 触发的重绘。
3. **contain 裁切的视觉误导**：横宽格 contain 后上下留空、竖长格左右留空，
   看起来可能像"裁错区域"。建议先打印 bbox 与缩略图实际显示区域比对。

**相关代码**：gui/timeline.html 的 `draw()`（缩略图块）与 `pvTick()`（预览大图，
预览大图用的是同一套 bbox 数学，可以交叉验证哪个环节错）。

## 2. GPT-SoVITS 在本机 GPU 推理必崩（已绕开）

两次复现均在"预测语义 Token"~70/1500 处进程无声消失，无 Python traceback，
判定为 CUDA 层面稳定性问题（该机同年另有一次渲染高负载死机记录）。
绕开方案：`research/tts_infer_cpu.yaml`（device: cpu, is_half: false），
CPU 全程稳定。F5-TTS 与 IndexTTS-2.5 在同一 GPU 上均稳定，说明不是显卡本体。

## 3. GPT-SoVITS 参考文本串音（已解决，注意别回归）

api 请求若传 `prompt_text`（参考音频的文字稿），短句推理可能把参考音频的
尾句"续读"出来（MaiMai 的"今天想听什么"）。解法 = 无参考文本模式：
`prompt_text` 传空字符串（TTS.py:1017 分支）。examples/make_audio.py 默认已传空。

## 4. torchaudio 2.10 强制走 torchcodec（已绕开）

本机缺 FFmpeg 4~7 共享 DLL（装的是 ffmpeg 8.0，torchcodec 不支持 8），
`torchaudio.load` 必炸。f5_batch.py / indextts_batch.py / indextts_test.py
里用 soundfile 完全替换了 torchaudio 的 load/save。若换环境可移除补丁。

## 5. IndexTTS fix_duration 语义陷阱（已移除该用法）

index-tts v2.5 的 `fix_duration` 是"含参考音频在内的总时长"（源码
`fix_duration - ref_sec`），按纯语音时长传入会把生成挤成空音频。
现方案不再使用强制时长，仅 duration_factor（0.5~2.0 语速控制）。

## 6. 系统代理劫持本地请求

本机有代理（127.0.0.1:7897）。访问本地服务（8765/8766/9880）必须绕过代理：
curl 用 `--noproxy "*"`，Python urllib 用 `ProxyHandler({})`，浏览器无影响。
ModelScope/HF 下载偶发限速时，杀掉重连即可恢复（断点续传）。

## 7. 其他备忘

- IndexTTS-2.5 License 为 bilibili 自定义协议：二创可用，商用前需过条款。
- GPT-SoVITS 启动命令（CPU 配置）：
  `cd E:\GPT-SoVITS-v4-20250529\GPT-SoVITS-v4-20250529 && runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c D:\AIGC\motion-comic-poc\research\tts_infer_cpu.yaml`
- edge-tts 连续合成（整段一次合成 + WordBoundary 字级时间戳切片）是
  解决"逐段合成韵律断裂"的通用方案，F5/其他无时间戳引擎可参考
  "按页整段合成 + 静音切分"（f5_batch.py v3）。
