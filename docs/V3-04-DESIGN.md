# V3.0-04 人像口型同步：实现与后端选择

本阶段实现 5–10 秒本地人像片段，不合成完整长视频。默认 A：原视频逐帧保留身体、服装、机位和场景，局部口部重绘。B 可输入授权图片，但当前只驱动静态图片的口型，不生成身体动作。C 由确定性镜头规划和 B-roll 接口组成；本阶段不自动合成长时间线。

## 2026-10-09 上游审计

| 后端 | 维护快照 | 代码与权重许可 | 官方推理显存信息 | 本阶段决定 |
|---|---|---|---|---|
| MuseTalk 1.5 | 未归档；HEAD `0a89dec45a0192b824e3cf4daf96c239440c5ed8`，提交 2025-09-26 | 代码 MIT；官方模型卡 MIT、项目说明允许模型商业使用；其他组件单独遵守许可 | 官方给出 Windows 4 GB / fp16 / 8 秒演示，不能当作本机性能保证 | 唯一实际实现的模型适配器，batch=2、fp16、25 fps |
| LatentSync 1.5 | 未归档；仓库 HEAD `a229c3948406bc2cf6eaf4873e662e70c6a04746`，提交 2025-06-20 | 代码 Apache-2.0；1.5 权重独立许可未完成核验，未将代码许可套用于权重 | 官方最低 8 GB | 理论可落在 12 GB 卡，但本阶段不部署，不声称效果优于 MuseTalk |
| LatentSync 1.6 | 同上 | 代码 Apache-2.0；**权重 OpenRAIL++** | 官方最低 18 GB | 本机 12 GB 容量不足；后续可在授权云 GPU 对比 |

维护日期来自 [MuseTalk 官方 GitHub API](https://api.github.com/repos/TMElyralab/MuseTalk/commits?per_page=1) 和 [LatentSync 官方 GitHub API](https://api.github.com/repos/bytedance/LatentSync/commits?per_page=1)。这是查询时的快照，不代表持续维护承诺。项目说明与硬件数字见 [MuseTalk](https://github.com/TMElyralab/MuseTalk)、[LatentSync](https://github.com/bytedance/LatentSync)。

许可依据：[MuseTalk LICENSE](https://github.com/TMElyralab/MuseTalk/blob/main/LICENSE)、[MuseTalk 模型卡](https://huggingface.co/TMElyralab/MuseTalk)、[LatentSync LICENSE](https://github.com/bytedance/LatentSync/blob/main/LICENSE)、[1.5 权重页](https://huggingface.co/ByteDance/LatentSync-1.5)、[1.6 权重卡](https://huggingface.co/ByteDance/LatentSync-1.6/blob/main/README.md)。MuseTalk 的 VAE、Whisper、face-parsing 在其 LICENSE 中列为 MIT，DWPose 列为 Apache-2.0；S3FD 权重来源单独记录，不将项目示例测试视频作为授权素材。

RTX 5070 属 Blackwell，不能照抄上游旧 CUDA 11.8 / torch 2.0.1 环境。[PyTorch 2.7 发布说明](https://pytorch.org/blog/pytorch-2-7/)确认 Blackwell 与 CUDA 12.8 支持；本适配器 doctor 会真正执行 CUDA 矩阵运算，并检查 MMLab 扩展导入。新的 PyTorch 与旧 MMCV/MMPose 组合仍需实机验证，不能只看 `nvidia-smi` 的 CUDA version。

## 接口与文件分工

- `lip_sync.py`：`LipSyncProvider`、能力/环境、输入与结果、配置和授权校验。同步 `generate(request, context)` 可在将来的 API 适配器内部提交/轮询；`status(job_directory)` 可独立读取状态。不支持的能力必须报错。
- `portrait.py`：资产清单读取、代理排序、人工审核、安全源区间、确定性规划、幂等使用记录。不会调用任何 AI。
- `lip_sync_provider.py`：独立 Python 子进程、依赖检查、模型/代码哈希、运行状态、实际帧进度。
- `musetalk_runner.py`：独立环境调用上游模型 API，不依赖 CreatorDNA 的 Python 环境。媒体 IO 全部用参数数组调用 FFmpeg，不使用上游未加引号的 shell 命令。CUDA 设备在导入 PyTorch 前隔离；运行期禁止网络连接。
- `lip_sync_workflow.py`：源/音频/清单/审核哈希快照、任务创建、素材准备、推理、AAC 合并和完整音视频解码；`lip_sync → final_check` 两个真实任务阶段。
- JSON Schema：配置、审核、授权、使用记录、输出清单均有独立 schema。未经人工检查的自动采样不会自动批准人像候选。

输出保存在任务独立 attempt/outputs 中。`portrait.mp4` 是模型结果与新音频的成品；`reference.mp4/png`、`speech.wav` 用于对照；`portrait.json`、`environment.json`、`backend/metrics.json`、`human-review.md` 用于追踪。失败输出不会登记为完成成品，但保留在 attempt 内供诊断。

## 复用与镜头连续性

代理排序综合单人脸采样比例、清晰度代理、显示尺寸和可用时长。`proxy_score` 不代表口型、姿态或身份质量，`quality_score` 必须人工给出。审核需注明 reviewer/evidence，检查正/近正脸、面部清晰、嘴无遮挡、适中头动、自然动作，并填写场景、重复动作组、开始/结束姿态和安全起止秒。

每段至少留 0.2 秒源帧余量。推理中音频特征帧不能超过源帧；任何人脸定位缺失都会失败，不允许跳帧、倒放、补静止帧或暗中循环。不同文件的相同源哈希、重叠源区间和相同 action_key 都受复用规则约束。每个 timeline 默认最多使用一次；明确允许更多次时，仍强制至少 30 秒 cooldown。position 是最终视频里的位置；多个独立视频使用不同 timeline，不推测跨视频播放间隔。

任务执行前原子写入 reserved 记录，避免排队任务同时使用同一画面。重试按 task_id 复用同一预留，不重复计数；中断或失败预留保留，防止另一个任务意外重复使用。成功标为 `generated_pending_review`，不是质量已通过。OS GPU 锁在进程退出后自动释放，避免同一 workspace 内多个模型任务抢显存；跨 workspace 不共享此锁。

切换优先同场景和匹配姿态；不匹配则返回 `b_roll_or_scene_transition_required`，规划的 `b_roll_hooks` 给出秒位置。此标记必须由后续编排实现，本阶段不会把一个不连续剪辑宣称为连续。素材不能覆盖请求长度时直接返回 `InsufficientReviewedNonRepeatingMaterial`。

原视频中的手势和表情语义不能被口型模型自动修正。应选择语义中性的动作；明显数数、指向、夸张表情需匹配新文案或用 B-roll 遮挡。烧录标题、字幕、主题背景也不会被本阶段移除。单纯背景相似不证明灯光、服装或动作一致。

## 环境安装与权重

CreatorDNA 环境不安装模型大依赖。为 MuseTalk 单独建立 Python 3.10 环境，按上述官方仓库固定 commit 安装；配置的 `python` 必须指向该环境的实际可执行文件。旧 GPU/CUDA 环境可遵循官方依赖步骤，Blackwell 必须使用支持 sm_120 的 PyTorch/CUDA，并解决对应 MMCV native extension。当前机器未完成这个组合的安装，不提供已验证的 Blackwell 一键安装声明。

```powershell
git clone https://github.com/TMElyralab/MuseTalk.git workspace/models/MuseTalk
git -C workspace/models/MuseTalk checkout 0a89dec45a0192b824e3cf4daf96c239440c5ed8
```

模型环境中按上游 `download_weights.bat`（Windows）或 `download_weights.sh`（Linux）获取模型。保持 `models/musetalkV15`、`sd-vae`、`whisper`、`dwpose`、`face-parse-bisent` 的官方布局，配置/权重完整清单由 `lipsync capabilities` 返回。推理不需要 SyncNet；本阶段没有 SyncNet 自动质量评分。额外从上游人脸检测器注明的 [S3FD 官方下载地址](https://www.adrianbulat.com/downloads/python-fan/s3fd-619a316812.pth)下载到 `models/s3fd/s3fd-619a316812.pth`。适配器将此文件复制到 attempt 的 Torch cache，避免首次推理联网下载。

修改 `examples/lipsync/musetalk.json` 的 repository/python 到实际绝对路径。FFmpeg/FFprobe 可走 PATH 或配置绝对路径；空间/中文媒体路径通过本地规范化和 Unicode 图像读取支持。首次推理会保存所用模型与源代码 SHA256；同一任务重试遇到模型内容变化会失败，须创建新任务。更新后端先重新验证，不自动升级权重。

## 云 GPU/API 替换路径

本机 GPU 容量本身够开展 MuseTalk 小测试，但当前缺独立兼容环境、模型权重和新口播音频。若 Blackwell/MMLab 依赖无法解决，可在**另行获得上传授权后**将同一 checkout 和 CreatorDNA 放到受控 Linux CUDA GPU 上运行同一 CLI，workspace 留在云主机。LatentSync 1.6 则选择至少 18 GB 空闲显存、建议 24 GB 以上的环境。仅资源容量建议，不是本项目的实测性能。

API 后端可替换 `make_provider`，实现能力、环境/凭据、提交/查询/下载、结构化错误及本地结果校验。必须增加服务商和 endpoint 范围的 cloud consent、持久任务 receipt、结果不确定时禁止自动重发计费 POST。当前配置**拒绝云端 provider**，local consent 不可作为上传许可。本次没有向第三方上传人像或声音。

## 实测判定

技术检查包括：单视频/音频流、完整解码、预期画幅/25 fps、实际帧数、时长、音视频开始时间与结束时间差。0.08 秒媒体时长容差用于帧量化和 AAC 编码，开始时间差不得超过 0.05 秒。此检查不能证明嘴唇与声音感知同步。

模型成功后仍须人工观察八项检查。metrics 记录真实推理帧数、GPU 名称、Torch 版本、模型/适配器耗时和 Torch 峰值 allocated/reserved；这些显存数值不包含其他桌面程序显存，不假装是整机占用。没有真实推理时不填任何模型耗时或质量通过结果。验证入口见 README 和 `V3-04-VALIDATION.md`。
