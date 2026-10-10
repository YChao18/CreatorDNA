# V3.0-06 验收报告（2026-10-10）

状态：本地渲染引擎、统一任务编排、CLI、可复用检查点、真实进度、成品自动检查与 MP4 输出已实现。**真实新 TTS + 人像口型的 Level 1–3 和人工质量验收未完成，环境阻碍见下文。合成工程样片不是真实声音/人物/作者风格证据。**

## 交付及执行方案

审计后选择复用 V1/V2 的文案接口、V3-02 TaskManager、V3-03 synthesize_stage、V3-04 prepare_task/generate_stage 和 V3-05 ProductionWorkflow。只新增 render.py、video_workflow.py、video_cli.py、工作流 Schema、验证入口与文档；原 TTS、口型和字幕实现保留。TaskManager 新增 script 阶段，并对运行中的视频流程只报告完成阶段数及实际子操作进度。

统一入口覆盖：新选题 create --topic；已有正文 create --script-file；时间轴 render --timeline；既有 tasks run/status/history/logs/cancel/resume；停止任务 video clean。MP4 位于任务 final_check 输出目录，同时有 verification.json 和人工检查表。修改字幕/背景/音轨可绕过 TTS 和口型，重用无关画面片段。README 和 examples/video 提供配置、授权、时间戳与真实套件说明。

## 本地工程运行（真实 FFmpeg，媒体为合成测试素材）

明确使用 testsrc2、图片色块、正弦音 WAV、synthetic speech 清单和合成字幕时间。没有请求云 API、真实 MuseTalk 或真实人脸模型。

| 合成时长 | 实测 MP4 时长 | 输出 | 工作流实际耗时 | 输出字节 | 自动检查 |
|---|---:|---|---:|---:|---|
| 15 秒 | 15.000 秒 | 180×320 / 25 fps / H.264 + AAC | 6.319 秒 | 1,052,554 | passed |
| 45 秒 | 45.000 秒 | 180×320 / 25 fps / H.264 + AAC | 6.096 秒 | 3,063,629 | passed |
| 150 秒 | 150.000 秒 | 180×320 / 25 fps / H.264 + AAC | 10.212 秒 | 10,200,875 | passed |

耗时是 TaskManager 全流程（已有合成声音复用、编排、分段合成、检查），不含 fixture 创建、真实 TTS/口型或 GPU 性能。15 秒用 1 秒分段，45/150 秒用 10 秒分段；不能直接据此比较速度，也不能推算真实 720×1280 或 1080p 推理吞吐。

可播放工程导出位于：

- `workspace/v3-06/exports/synthetic-engineering-15s.mp4`
- `workspace/v3-06/exports/synthetic-engineering-45s.mp4`
- `workspace/v3-06/exports/synthetic-engineering-150s.mp4`

汇总 `workspace/v3-06/engineering-summary.json`，每个导出有同名 verification.json，原始任务/checkpoints/logs 保存在各 engineering-* 工作区。人工查看了测试图与 B-roll 的采样帧，确认测试素材/字幕可显示；这是工程视觉 QA，不是人物或作者风格评价，完整人工验收仍 pending。

## 自动测试与修复

使用项目现有 `.venv`，按 AGENTS.md 运行 `python -m unittest discover -s tests -v`；系统默认 Python 未安装 jsonschema/项目包，不能代替项目 runtime。最终运行 **165 项，162 项通过，3 项真实集成 skip，无失败**，耗时 58.537 秒。完整日志见 `workspace/v3-06/unittest-final.log`。

新增工程测试覆盖：CLI/配置、非整数帧率的全局网格不丢帧/重复边界、真实 FFmpeg 背景/前景与 B-roll 像素、音轨、字幕、缓存、局部关键词修改不重算无关片段、已有文案/声音复用、排队后输入变更拒绝、cover/crop/anchor/fade、近黑画面阻止成片发布、附加音轨修改仅重混音、清理后成果哈希与幂等读取、新主题正文提取和 Skill 固定、口型分段失败仅重试未完成段。全部媒体/Agent/模型替代品明确标注 synthetic，不计真实质量证据。

实际测试发现并修复：Windows 260 字符路径限制（任务目录使用原生 extended path）；不同源/目标 fps 的终点重采样少一帧（最多一个输出末帧正常延展并检查实际帧数）；Windows extended path 下 FFmpeg 对 `.wav.raw` 选择 rawvideo demuxer（原有 TTS 下载临时文件保留真实媒体扩展名）。最后一项是文件命名兼容性修复，未改 TTS Provider 或收费逻辑。

31 个 JSON Schema 已通过 Draft202012Validator.check_schema，模块 compileall 与 git diff --check 通过。wheel 构建成功，包含新增渲染/编排/CLI 模块和工作流 Schema；检查产物在 `workspace/v3-06/wheels/`。已有 V1/V2/V3-01 至 05 回归仍包含在全仓测试中。

## 真实 Level 1–3 阻碍

实际环境检查保存在 `workspace/v3-06/real-preflight.json` 与 `lipsync-preflight.json`：

- MINIMAX_API_KEY、MINIMAX_CN_API_KEY、DASHSCOPE_API_KEY 均未设置，只记录存在性，没有读出/打印密钥。
- 指定 MuseTalk repository、模型/辅助文件以及独立 Python 环境缺失；doctor 返回 ready=false / FileNotFoundError。
- 未发现真实新生成 speech.json；验收过程中产生的清单均为显式 synthetic。
- V3-04 的真实创作者候选已有媒体分析，但完整人工人像审核尚未完成，不能自动给 approved=true。

因此真实 Level 1 标记 blocked，Level 2/3 按依赖未运行，未上传人像或声音，未产生真实 TTS/推理费用；真实账单、GPU 峰值和人物质量数据为未测。没有用参考原声、测试图或正弦音宣称真实链路通过。

准备凭据/已注册授权音色、真实 MuseTalk 环境/权重、已审核人像/生成授权和具真实时间戳的字幕后，填写 examples/video/real-suite.json 对应 level1/2/3.txt，运行 `python scripts/validate_v3_render.py --real-suite ... --workspace ...`。每级必须新 TTS 和真实口型，实测时长分别 10–20、30–60、120–180 秒，前一级技术检查失败后不运行更长级。也可显式开启 tests/test_video_integration.py；默认 skip，不算 passed。

## 人工验收与改进

人工待检查人物外貌、自然声音、口型感知同步、重复动作、B-roll 含义、字幕字体/清晰度及参考作者的视频风格。自动解码/时长/黑屏检查不判断这些问题。静态图接口不生成自然身体动作，原素材烧录字幕/主题包装不会被移除。

后续优先补齐真实测试条件和各级人工记录，再处理姿态 hook 自动要求 B-roll、参考观察到生产配置的明确映射、统一 CPU/RAM 峰值采集、账单联查、缓存 LRU 和跨任务收费去重。当前非转场黑屏检测可能阻止合法黑色主题素材，需人工核对；长片的源素材复制占用较多磁盘。接口支持外部对齐器，但没有随仓库安装新的强制对齐模型；缺可靠时间戳可在收费后停于 composition，安全恢复不会重复已完成 TTS。
