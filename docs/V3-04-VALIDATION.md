# V3.0-04 验证报告（2026-10-09）

状态：接口、任务接入、候选选择和工程媒体链路已实现；**真实 MuseTalk 推理、5–10 秒真实口型样片和人工人物质量验收未完成**。用户在本次会话明确授权材料进行本地处理，同时确认尚未生成新 TTS 口播音频，要求先实现功能。没有用原声音或合成测试图代替新音频/模型验收，没有上传人像或声音。

## 环境与客观阻碍

- `nvidia-smi`：RTX 5070 / WDDM，12,227 MiB 总显存，初次检查约 2,438 MiB 被桌面程序使用；驱动 591.74，报告 CUDA 13.1。这个 CUDA 数字不是已安装推理 runtime。
- CreatorDNA `.venv`：Python 3.14.6；FFmpeg/FFprobe 可用。OpenCV/Pillow 可运行素材分析。
- Torch、Diffusers、Transformers、librosa、OmegaConf、MMPose、MMDet、MMCV、einops 及模型运行环境未安装。示例配置的独立 Python 和 MuseTalk checkout/权重尚不存在。
- 未发现任何 V3.0-03 `speech.json`；现有 workspace WAV 为源素材转录提取音频，不能作为全新 TTS 样例。
- Blackwell 需要支持 sm_120 的 PyTorch/CUDA，旧 upstream 环境不能直接套用；MMLab 二进制组合未验证。12 GB 不满足 LatentSync 1.6 的官方 18 GB 要求，不能据此宣称本机没有可用 GPU。

诊断实际保存在 `workspace/v3-04/portrait-preflight/environment.json`。`ready=false` 是未准备好后端，不是模型质量不合格。

## 真实素材分析（不是口型推理）

只发现此前 V3.0-01 的合成验证清单，未找到 creator-001 的真实视频资产库。因此重用现有 V3.0-01 分析代码生成真实清单；源素材保持只读。

```powershell
.\.venv\Scripts\python.exe -m creator_dna analyze-video --workspace workspace/v3-04 --input materials/creator-001 --creator creator-001 --sample-interval 5 --max-frames 40 --segment-seconds 12
```

结果：18/18 视频成功，约 1,993.525 秒，260 候选，189 个 A 建议，166 个候选原始时长至少 5.2 秒。**166 不等于可用人像数量或可覆盖视频总长度**：可能含侧脸、重复动作、不兼容烧录文字/背景，仍需完整片段审核。人工适合度审核通过数目前为 0；未自动填入 approved=true。

真实清单：`workspace/v3-04/creators/creator-001/runs/video-analysis-184e5d0c3be74773bb6cd557512e8478/library.json`。报告、联系图和原始采样证据同目录。排序与审核模板：`workspace/v3-04/portrait-preflight/ranked-candidates.json`、`review.json`。

| 代理高分候选（待审核） | 来源简述 | 起止秒 | 显示画幅 |
|---|---|---|---|
| s-42edbc76ba6e46ea-shot-00001-clip-00002 | 机器人也会失业 / 095025 | 24.083–36.083 | 720×1280 |
| s-42edbc76ba6e46ea-shot-00002-clip-00000 | 同源不同段 | 46.875–58.875 | 720×1280 |
| s-42edbc76ba6e46ea-shot-00002-clip-00002 | 同源不同段 | 70.875–82.875 | 720×1280 |
| s-466b1d0e164ebd22-shot-00002-clip-00000 | 今天起制造业已经被 AI 变革 / 095034 | 7.333–19.333 | 720×1280 |

查看第一候选采样帧：可见黑色上衣、坐姿、近正脸和完整画面中的人物，但脸占比有限；源片带上方主题文字、背景图片和下方烧录字幕。此帧支持开展 A 的候选检查，不证明整段无遮挡、动作与新语义一致或口型质量合格。烧录文字会被原样继承，建议优先补充无字幕原始口播机位素材；本阶段不做字幕擦除或主题背景重建。

方案判断：A 优先验证真实源画面口部局部重绘；B 的静态图口型接口已实现，动作驱动未接入，真实 A/B 比较未运行；C 的非循环多候选规划与 B-roll 秒位置接口已实现，但尚未形成完整视频。

## 工程测试

在激活项目 `.venv` 后执行规定命令：

```powershell
python -m unittest discover -s tests -v
```

最终全仓验证：运行 **130 项，128 项通过，2 项真实集成测试 skip**，耗时 33.075 秒。原有全部功能回归正常。跳过项为真实付费云 TTS 和真实本地口型模型，不算质量已通过。完整日志保存在 `workspace/v3-04/portrait-preflight/unittest-final.log`。

新增 15 项工程测试覆盖 schema/CLI 参数、采样不可自动审核、证据区间/哈希/清单绑定、重复动作/重叠源时间/素材不足、确定性多候选、cooldown、镜头转换 B-roll hook、幂等预留、GPU OS 锁、授权范围、音频长度、TTS 清单哈希/路径、源目录写入隔离、排队后源变化、缺真实后端时持久诊断与安全 resume，以及 FFmpeg 全音视频媒体链路。新增 1 项真实模型集成测试目前 skip。

`tests/lip_sync_fixtures.py` 是显式合成工程 fixture，只验证任务/文件/音视频时间戳/125 帧完整解码，不产生真实人脸，也不用于“已实现真实口型同步”结论。它只能通过 tests 的显式执行器注入调用，CLI 不提供假后端。

## 真实样片复现步骤与未完成项

1. 按 `V3-04-DESIGN.md` 准备独立兼容模型环境和离线权重；运行 lipsync doctor 检查。当前阻碍是环境/权重缺失，不是没有显卡。
2. 用现有 V3.0-03 生成全新语音，保存真实 speech.json。此步骤可能需要已授权声音资产/服务凭据和单独云端授权，本阶段不隐式调用云 TTS。
3. 完整播放候选，填写 portrait review 和安全区间；如果无合适段，补录授权口播素材，不能循环凑时长。
4. 根据真实新音频和选中源视频 SHA256 创建 local consent；会话已有本地授权，不要求再次取得同一授权，文件用于把具体输入与授权依据固定下来。
5. 运行 README 的 generate --run，选择 5–10 秒，确认 lip_sync/final_check 状态、视频尺寸、解码和资源 metrics。
6. 人工对照 reference.mp4 与 portrait.mp4，记录口型、脸部稳定、身份、原动作、牙齿/嘴边缘、手势/表情语义、切镜光影与感知同步。可对同一音频另行用授权 image 测静态图口型；不得把静态图口型称为完整动作驱动。

目前没有真实样片文件、模型 GPU 峰值、推理耗时或人物质量分数。所有这些结果明确为未测，不填猜测数值。不得将本文中的工程测试通过转述成真实口型或身份稳定通过。
