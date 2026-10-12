# MuseTalk 本机部署与真实样片

完成日期：2026-10-10（Asia/Shanghai）。本次已实际部署、加载模型并生成真人口型视频；下面的结果来自真实 MuseTalk，不是测试替身。人像候选按用户要求默认自动选择，不再等待人工审核确认。

## 已部署环境

| 项目 | 实际配置/验证 |
| --- | --- |
| Python | `workspace/models/musetalk-env/Scripts/python.exe`，独立 Python 3.10.20 |
| GPU | NVIDIA GeForce RTX 5070，compute capability 12.0；真实 CUDA 矩阵运算通过 |
| Torch | 2.7.1+cu128，CUDA runtime 12.8，包含 sm_120；torchvision 0.22.1+cu128、torchaudio 2.7.1+cu128 |
| MuseTalk | 上游代码固定 `0a89dec45a0192b824e3cf4daf96c239440c5ed8`，1.5 模型；代码位置 `workspace/models/MuseTalk` |
| MMLab | mmpose 1.3.2、mmdet 3.2.0、mmengine 0.10.7、mmcv 2.1.0 |
| MMCV 构建 | Visual Studio 2026 本地构建 CPU 原生算子；实际 import 和 CPU NMS 通过。MuseTalk/DWPose 主推理仍用 CUDA，不宣称已编译 MMCV CUDA 算子 |
| 兼容包 | numpy 1.23.5、opencv-python 4.9.0.80、diffusers 0.30.2、transformers 4.39.2、huggingface-hub 0.30.2；完整版本清单在 workspace/models/requirements-musetalk-installed.txt |
| 媒体工具 | 本地 FFmpeg/FFprobe 6.1.1 |
| 环境一致性 | 独立模型环境 `pip check`：No broken requirements found |
| 后端诊断 | `workspace/models/environment-verified.json`，ready=true；11 个必需模型/配置文件完整 |

使用已有配置即可复用，无需重新安装或下载：

```powershell
.\.venv\Scripts\python.exe -m creator_dna lipsync doctor --config workspace/models/musetalk.json
```

`workspace/models/musetalk.json` 的 repository/python 为本机绝对路径。迁移机器时改为新机器路径并重新验证 GPU/扩展；仅复制 JSON 路径不能安装运行环境。

## 权重来源与完整性

来源按上游 MuseTalk 下载脚本选择，固定 Hugging Face revision；网络不稳定时使用该上游脚本提及的 hf-mirror 下载公开文件，并与原站的 SHA-256 比较。PyTorch wheel 与官方索引 SHA-256 比较；S3FD 来自上游检测器指定的 adrianbulat.com 链接。分块完整拼接后才成为可加载文件，未将部分下载当作完整权重。

完整实际文件哈希与大小保存在 `workspace/models/model-files-verified.json`；安装 wheel、下载记录与构建日志保存在 workspace/models。模型包括 MuseTalk v15、SD VAE、Whisper tiny、DWPose、BiSeNet face parsing、ResNet18 与 S3FD。当前推理离线，缓存只写任务目录，不上传人物。

## 本次必要兼容修复

1. PyTorch 新版默认 weights_only 加载与旧 DWPose NumPy/HistoryBuffer 元数据不匹配。桥接脚本仅允许所需的具体元数据类型，保留 weights-only 加载，未全局开放任意 pickle。
2. 上游 ResNet18 为旧 TAR checkpoint，weights-only 无法读取。仅当原文件 SHA-256 精确匹配固定官方权重时进行一次可信旧格式读取，核验其为纯 tensor state，并在当前任务的 cache 写现代 tensor-only 文件。原权重不改写；其他来源/哈希的旧文件拒绝这个兼容路径。
3. PNG 编码模型视频时缺少 SAR 标记，曾被成品检查拒绝。编码器增加 setsar=1，最终视频实测 SAR=1:1。未降低最终检查标准。

原失败尝试保留，最终成功为同一任务的 attempt-005。失败不发布成片、不登记为成功。

## 真实样片结果

- 使用现有真实 `QianHao_GPT` TTS 音频的前 8 秒，本轮未再次调用 TTS 或声音复刻。
- 源：creator-001 的“机器人也会失业”视频，候选 `s-42edbc76ba6e46ea-shot-00001-clip-00002`，源起点 24.083333 秒，预留 0.2 秒余量。
- 自动选择状态为 automatic_candidate，review=null、quality_score=null、姿态 unknown；没有伪造人工审核记录。源/音频授权和哈希、区间使用账本、逐帧定位、无循环规则仍执行。
- 任务：`task-deb3bcf9aefa4f18ac5ce9fcde09e4a7`，lip_sync / final_check 均 completed。
- 输出：`workspace/creators/creator-001/outputs/QianHao_GPT-MuseTalk-8s.mp4`。
- 技术结果：720×1280、25fps、200帧、H.264/AAC，音频/视频时长均8.000秒，起始时间差0，SAR=1:1，全音视频完整解码通过。
- 真实后端记录：200帧；模型内耗时47.703秒、Provider耗时54.138秒；Torch峰值allocated=7,407,921,152字节（约6.90GiB）、reserved=7,696,547,840字节。此显存不包含其他桌面程序。
- 运动模式：preserved_source_sequence，source_looped=false、dropped_frames=0。原素材SHA-256不变。
- 抽查0.5/2.5/4.5/6.5秒源/结果面部对照，未见明显破损，嘴部由模型重绘。不能以抽帧替代感知同步或作者风格质量评分；没有给出此类分数。旧标题、烧录字幕、主题背景仍随源片保留，与新口播字幕可能不一致。

setup、授权、对照帧、原片预览、测试日志位于 `workspace/creators/creator-001/runs/musetalk-setup-7d10cf2668034d518b5c2de43fec2caa/`；实际 portrait.json/metrics/verification 与失败日志在 task 的各 attempt 中。

最终全仓回归：`python -m unittest discover -s tests -v` 使用项目 .venv，172项、169通过、3项opt-in真实服务测试跳过、0失败，57.548秒。这个独立真实样片任务不计入默认跳过的测试，也不能用于宣称完整视频Level1–3或风格验收完成。

后续完整视频仍需解决长字幕区间细化、视觉样式/B-roll配置及人物切口到编排的约束。当前已能自动创建和执行短真人口型样片，没有人工候选确认门槛。
