# CreatorDNA V3.0 全项目审计报告

审计日期：2026-10-10（Asia/Shanghai）。对象：`D:\Project_Agent\CreatorDNA` 当前工作树，包版本 `0.3.0`。本报告依据源码、配置、现存运行产物及本轮执行结果；历史开发报告只作线索。

## 1. 结论与统计口径

**V1/V2 的工程流程和 V3 的本地制作骨架已实现，V3“真实新声音 + 真实人物口型 + 作者风格成片”的端到端流程当前不具备运行条件，也未达到正式验收条件。**

当前不是单纯“补一个 Key 就全部可用”：还缺真实注册音色、MuseTalk 独立环境/权重、人工通过的人像候选、完整工作流配置，以及部分路径/收费前检查/镜头连续性衔接。已有合成 MP4 只能证明本地合成工程链路。

按下表 **27 个功能模块**计数，标记允许交叉，不能相加为总数：

| 指标 | 数量 | 模块 ID | 含义 |
| --- | ---: | --- | --- |
| 本轮实际执行验证通过 | 14 | 01、02、05、10、12、13、14、19、21、22、23、24、25、27 | 本地功能/确定性合同已运行；部分输入为明确标注的合成样本，绝不表示真实 AI/TTS/人像质量通过 |
| 需要补充配置或输入材料（A） | 11 | 13、14、15、16、17、18、19、20、21、22、26 | 凭据、音色、审核、素材、对齐和配置路径等 |
| 存在实现/集成缺口（C） | 7 | 13、14、15、20、22、26、27 | 包括已复现缺陷和源码确认的功能边界，详见 C-01～C-07 |
| 仍需关键真实接口或生产链路验证（D） | 17 | 03、04、06、07、08、09、11、13、15、16、17、18、19、20、21、22、26 | 当前模型账户、真实内容/视觉质量或模块组合尚未验收 |

“执行验证通过”统计的是明确可执行的本地部分，不把外部服务替身计为该外部能力通过。模块同时具有本地通过与真实验收待测状态是正常的。唯一后端缺环境主要集中在模块 20；系统 Python 和依赖冲突另见 B 类。

本轮只新增本报告和 `CONFIGURATION_CHECKLIST.md`；未修改功能逻辑、README、配置、素材或现存业务产物，未提交/推送 Git，未发起云端模型推理或收费 API 请求。本地 ASR 推理已实际执行。测试使用自清理临时目录；本轮冒烟产物也已随临时目录清理。现有大量未提交开发变更保持原状。

## 2. 架构与真实调用链

源码是 Python 模块化 CLI，无 Web、数据库或后台调度服务。41 个顶层 Python 源模块中，CLI 注册 `tasks / tts / lipsync / production / video`，V1/V2 命令继续保留（`src/creator_dna/cli.py:19`）。确定性媒体/存储与模型解释总体分开：Agent、Transcriber、VideoAnalyzer、TTSProvider、LipSyncProvider、AlignmentProvider 均有明确接口。

| 链路 | 实际接线 | 判断 |
| --- | --- | --- |
| V1 | CLI → Workflow → scan/MediaProcessor → CodexAdapter → Schema/原文证据 → SkillManager | 已接通；蒸馏形成草稿，确认后可生成新稿；生成不回读原媒体/Meta-Skill |
| V2 | CLI → QualityWorkflow → evaluator/optimizer 包资源 → 证据核对/有限修订 → batch/compare/feedback → SkillManager 门禁 | 已接通；原始评分和人工覆盖分开，development/acceptance 有隔离及发布条件 |
| V3 分析准备 | analyze-video 或任务执行器 → LocalVideoAnalyzer → VideoAssetWorkflow → library → production profile/assets、lipsync review | 有真实数据合同；审核、分类、授权和生产参数仍需人工准备 |
| V3 新视频 | video create → create_video_task → script → tts → 可选 lip_sync → composition → render → final_check | 执行器实际注册于 `tasks.py:34` 附近，不是空阶段；真实收费/人物分支未执行验收 |
| V3 重渲染 | video render → rebuild → render → final_check | 工程测试真实执行 FFmpeg，不重新调用 Agent/TTS/口型 |

统一 `video create` **不接受参考视频文件夹并自动完成前置分析、审核、音色注册和资产准备**；它从已有 `profile/assets/voice_asset/portrait review` 等输入开始。素材分析已通过合同连接生产模块，但不是这个任务计划中的自动前置阶段（`video_workflow.py:56`）。跨 workspace 操作必须显式传入正确路径和同一 creator；默认 `workspace` 里已有 Skill，不代表 `workspace/v3-04` 自动有该 Skill/Voice Asset。

### 模块状态矩阵

表中 L=本轮本地执行通过，A=配置/材料缺失，C=实现/集成缺口，D=关键真实验证缺失；状态按上节 ID 统计。

| ID | 版本/模块 | 实际代码及集成状态 | 本轮验证与生产边界 | 标记 |
| --- | --- | --- | --- | --- |
| 01 | V1 文件夹、转写导入、缓存 | input.py/media.py/workflow.py；原文件扫描、同名匹配、哈希、缓存 | 本地测试通过；txt/srt/json 合同和缓存失效有覆盖 | L |
| 02 | V1 本地 ASR | WhisperTranscriber，CLI faster-whisper/none | 真实 56.733333 秒参考片经实际 MediaProcessor 生成 10 段/100 字，第二次命中缓存；未评估识别准确率 | L |
| 03 | Agent 接入 | CodexAdapter，经 stdin、结构化 Schema、隔离推理目录 | CLI/认证可发现；本轮不作推理；账户额度/实际模型权限待测 | D |
| 04 | Meta-Skill 蒸馏 | 随包资源加载，Schema + 实际转写引文 + high 跨样本检查 | 替身验证工程拒绝条件；现存 creator-001/v002 证据重核通过；新真实推理未重跑 | D |
| 05 | Creator-Skill 管理/独立导出 | SkillManager 草稿、确认、修订、导出、差异、回滚 | 测试通过，creator-001/v002 与 demo/v001 已批准包完整性重读通过；导出不依赖原媒体 | L |
| 06 | 新选题文案 | V1 Workflow.generate；V3 script_stage 提取纯正文 | 接线/Schema/规则引用测试通过，真实新稿效果待测 | D |
| 07 | V2 风格/质量/原创性评价 | evaluation.py + QualityWorkflow + evaluator Skill | 替身验证证据/评分决策；没有客观风格相似度或全文查重证明 | D |
| 08 | V2 有限修订/复评 | cycle、max_revisions、optimizer Skill | 替身验证次数/中断/反馈绑定；真实创作者修订质量待测 | D |
| 09 | V2 固定集/版本对照 | batch、compare_data、同配置/同模型条件核对 | 工程门禁通过；未做真实独立验收集对照 | D |
| 10 | V2 人工反馈记录 | feedback + 独立 human-view，追加保留原评分 | 确定性测试通过；不把测试署名当作真实人工意见 | L |
| 11 | V2 Skill 优化/发布门禁 | 重复 development 偏差 + 人工反馈 → 新候选 → acceptance 确认 | 替身覆盖门禁；真实优化和产品质量提升未验收 | D |
| 12 | V3 视频参数/切镜/采样/资产索引 | LocalVideoAnalyzer/VideoAssetWorkflow，本地 FFmpeg/Pillow/OpenCV | 真实片 720×1280、56.733333 秒、4 采样帧/2 候选镜头成功；已有真实库 18 源/260 候选及源哈希重核通过 | L |
| 13 | V3 Video Style Profile | 模板显示参数 + 像素镜头统计 + 人工 evidence | 合同/模板测试通过；现存 profile 人工观察数 0、coverage=0；字体/颜色等未自动落实生产配置 | L/A/C/D |
| 14 | V3 声音参考处理 | VoiceAssets.add_reference，本地提取/规范化/来源追踪 | 本地媒体工程测试通过；没有真实云授权 reference/voice；源目录隔离缺口已复现 | L/A/C |
| 15 | Qwen 声音复刻/管理 | create/list/delete/import，scope + target_model 绑定 | HTTP 协议替身测试；无 Key/音色，真实注册未测；fallback 审核闭环缺口 | A/C/D |
| 16 | MiniMax 声音复刻/管理 | upload → voice_clone，Voice ID 保存、激活截止、列表/导入 | HTTP 协议替身测试；无 Key/音色；真实上传/注册/七天激活未测 | A/D |
| 17 | Qwen 新文案 TTS | 真实 HTTP 适配器 + 分段 receipt + 本地 PCM | 无 Key/音色，无真实合成；长稿还需外部对齐 | A/D |
| 18 | MiniMax 新文案 TTS | 同步 hex 音频 + 句级字幕 + 分段账本 | 无 Key/音色，无真实合成/字幕响应/计费行为验证 | A/D |
| 19 | 人像审核/非循环选择/预留 | portrait.py，5–10 秒 + 0.2 秒余量，action/pose/cooldown | 确定性测试通过；实际审核表 189 项，rank_candidates 可用数 0 | L/A/D |
| 20 | MuseTalk 真实口型 | 单一实际 Provider，独立 Python/CUDA bridge；视频保留源动作，图片不生成身体动作 | doctor ready=false，11 个模型/配置文件和 7 个代码路径缺失，独立 Python 不存在；路径依赖 CWD | A/C/D |
| 21 | 背景/B-roll/授权素材 | production_assets、Agent 导演/字面标签 fallback、前景/背景轨 | 实际本地选择/合成工程测试通过；真实库 260 项仍是待分类 video，原声验证截图不是新选题素材验收 | L/A/D |
| 22 | 字幕/真实时间轴 | speech_input、provider_cues、外部 timing/AlignmentProvider、ASS | PCM/时间/全文/高亮合同和 FFmpeg 实际解析通过；新 TTS 对齐缺输入且错误可发生在收费后 | L/A/C/D |
| 23 | 本地成片渲染/缓存/混音 | render.py，FFmpeg CFR 分段、H.264/AAC、ASS、本地缓存 | 真 FFmpeg 工程测试通过；输入是测试图/正弦音，不能证明真实人物效果 | L |
| 24 | 最终完整性检查 | verify_stage 后才写 final.mp4 | 实测帧数、画幅、时长、音轨/起点、解码、黑屏检查测试通过；human_review 仍 pending | L |
| 25 | 任务进度/错误/恢复 | TaskManager/worker，实测事件、心跳、停止进程树、哈希检查点 | Windows 测试通过；付费未知结果恢复及模型恢复只由替身验证，不是远端幂等证明 | L |
| 26 | V3 统一新视频链路 | video_workflow 薄编排，既有语音/新语音/重渲染分支 | 已有声音分支工程贯通；真实 Level 1–3 缺配置，切换 hook 和收费前检查不足 | A/C/D |
| 27 | 配置校验/诊断 | 模块 JSON + CLI 参数 + 环境变量，通用 doctor/lipsync doctor | 真实运行通过；无 dotenv 自动加载，无一次性全流程 readiness 诊断 | L/C |

## 3. 外部服务和配置事实

| 服务/能力 | 实际 Provider/模型 | 配置入口/环境变量 | 当前状态 | 切换与真实验证 |
| --- | --- | --- | --- | --- |
| 文案、蒸馏、评价、优化、语义导演 | Codex CLI；模型沿用其配置或显式 model | CLI `--codex-bin/--model/--timeout`；工作流 `agent.binary/model/timeout`；凭据由 Codex 管理 | Node 24.16.0、Codex 0.146.0，CLI 认证状态为已认证；不输出认证值，不认定额度有效 | AgentAdapter 可扩展；目前 CLI 实际只构造 CodexAdapter。本轮未调用；历史 7 调用运行记录只作历史接入线索 |
| ASR | 本地 faster-whisper 1.2.1 / small，CPU int8 | `--asr/--asr-model/--language/--device/--compute-type/--ffmpeg` | small 本地缓存能真实转录；另可见 base/medium 缓存目录但本轮未加载 | Transcriber 可扩展，CLI 当前 faster-whisper/none；不需 ASR API Key；准确率待人工核验 |
| Qwen 声音复刻 | qwen-voice-enrollment，target_model=qwen3-tts-vc-2026-01-22 | `examples/tts/qwen.json` 的 provider/model/base_url/api_key_env；`DASHSCOPE_API_KEY` | 配置静态通过，进程/User/Machine 三层变量均缺失；默认 workspace 无真实音色资产 | 可切 MiniMax；Voice ID 不能跨 Provider/端点/模型直接复用；真实接口未测 |
| Qwen 合成 | qwen3-tts-vc-2026-01-22，HTTP 同步 URL 音频 | 同上；默认北京 `https://dashscope.aliyuncs.com/api/v1` | 无 Key/音色；速度 1.0、emotion=null、subtitles=false；该适配器不支持其他 VC 模型 | 非默认不支持参数报 Unsupported；成品格式/采样率可本地转换；真实接口未测 |
| MiniMax 复刻/合成 | voice_clone + speech-2.8-hd（示例），另允许代码中 8 个 speech 模型 | `examples/tts/minimax-cn.json` → api.minimax.cn / `MINIMAX_CN_API_KEY`；国际 minimax.json → api.minimax.io / `MINIMAX_API_KEY` | 两端配置静态通过；两个变量三层均缺失；未有注册音色/首次激活 | 同一 TTSProvider/Receipt 接口可切；需匹配账户、环境变量名、端点；真实接口未测 |
| 人像/口型 | 本地 MuseTalk v15（1.5 bridge） | `examples/lipsync/musetalk.json`：repository、python、gpu_id、batch_size、float16、parsing_mode、ffmpeg/ffprobe | 示例目录/独立环境不存在，ready=false | LipSyncProvider 可扩展，但工厂/Schema 当前只有 MuseTalk；没有 LatentSync、远端口型 API 或云回退 |
| 字幕对齐 | 外部已存在的对齐器或已生成 subtitle-timing；MiniMax 句级字幕可直接输入 | 工作流 `timing` 或 `alignment_command`；独立 production plan `--timing` | 仓库没有内置可运行对齐模型；现有 timing 来自原声音/合成测试，不能用于新 TTS 音频 | AlignmentProvider 可注入；CLI 不会自动运行 faster-whisper 作为新语音强制对齐；真实新语音未测 |
| 视频分析/合成 | FFmpeg/FFprobe + Pillow/OpenCV，本地 | 各 CLI `--ffmpeg/--ffprobe`、TTS/口型/渲染配置的同名字段 | 本轮实际通过 | 不需要新增云模型 API |
| 背景/B-roll | 已有本地授权资产；语义选择由 Codex 导演承担 | assets 清单 rights/topics/kind/description；production_config；工作流 director | 不联网找素材、不生成背景图、不抠像/补全背景、不擦除烧录字幕 | 无独立图片/搜索服务 Key；这些未实现的能力不能标为待填某个 API Key |

截至审计日，官方文档能支持目前核心 HTTP 路径、模型系列和复刻输入字段；这只证明文档协议核对，不证明账号已调用成功。Qwen 复刻使用 `qwen-voice-enrollment` + `input.action=create/audio.data/target_model/preferred_name`，同步合成使用 `input.text/voice`；MiniMax 使用 `/v1/files/upload`、`/v1/voice_clone`、`/v1/t2a_v2`，本实现非流式、句级字幕。来源：[Qwen 复刻](https://www.alibabacloud.com/help/en/model-studio/voice-clone-design-http-api)、[Qwen 模型/地域](https://www.alibabacloud.com/help/zh/model-studio/non-realtime-tts-user-guide)、[MiniMax 合成](https://platform.minimax.io/docs/api-reference/speech-t2a-http)、[MiniMax 复刻](https://platform.minimax.io/docs/api-reference/voice-cloning-clone)。

TTS 配置以 `api_key_env` 指定变量名，不保存 Key；Voice scope 使用“端点 + 变量名”，不能检测同名变量更换账户。换账户应改用新的变量名及重新注册/核验音色。默认 workspace 的 creator-001/demo 下 `voice-*.json` 数量均为 0；全 workspace 只找到 3 份 speech.json，位于明确标记的 synthetic-source 中，没有发现真实新 TTS 清单。

超时/重试不是缺失：`request_timeout`、`request_interval`、`max_attempts`、`retry_delay` 已实现。只对明确配额拒绝有限重发，网络未知结果落盘为 inflight/outcome_unknown，阻止自动再 POST；拿到 receipt 后可只下载/处理。注册未知结果需要人工核对；不支持的 query/update/async 操作显式 Unsupported。这是支持边界，不是伪实现。真实限流、网络恢复、账单和云端激活仍待验证。

## 4. 实际环境与依赖

| 项目 | 实测状态 | 证据/限制 |
| --- | --- | --- |
| 默认 python | 已安装但 CreatorDNA 环境缺失 | `D:\Program Files\Miniconda3_install\python.exe`，3.14.6；直接 unittest 有 12 个导入错误，缺 jsonschema/creator_dna，未进入业务断言 |
| 项目 .venv | 已安装并验证 | 3.14.6；creator-dna 0.3.0 editable 指向当前项目；`include-system-site-packages=true`，因此会继承基础环境依赖 |
| 基础包 | 已安装并导入验证 | jsonschema 4.26.0、PyYAML 6.0.3 |
| 视频包 | 已安装并执行验证 | Pillow 12.3.0、OpenCV headless 4.14.0；真实采样/联系图、人脸检测代码能执行，但不证明人像识别准确 |
| ASR 包 | 已安装并执行验证 | faster-whisper 1.2.1、CTranslate2 4.8.1、PyAV 18.0.0、ONNX Runtime 1.27.0；本地 small 模型本轮加载成功 |
| 依赖冲突 | 存在，P1 | `pip check` 退出码 1：huggingface-hub 1.22.0 要求 click>=8.4.2,<9，当前 8.4.1；本轮 ASR 能跑不等于整个依赖图无冲突 |
| Node/Codex | 已安装，认证状态可读；模型请求待验证 | Node 24.16.0、Codex CLI 0.146.0；Node 用于 npm Codex wrapper，非渲染业务必需的另一套运行框架 |
| FFmpeg/FFprobe | 已安装并实际验证 | `D:\Program Files\ffmpeg\bin`，6.1.1；libx264、AAC、libass、本地混音/完整解码相关测试执行通过 |
| GPU/驱动 | 已安装并枚举；模型 runtime 未验证 | RTX 5070，驱动 591.74，总显存 12,227 MiB；不将驱动信息等同于 Torch/CUDA/MMLab 已可推理 |
| MuseTalk 代码/模型 | 示例路径未安装，P0 | 缺 11 个模型/配置文件 + 7 个代码路径，详见 `lip_sync_provider.py:13` 和 `:18`；不是发现文件后就能确认版本兼容 |
| MuseTalk 独立 Python | 示例路径未安装，P0 | `workspace/models/musetalk-env/Scripts/python.exe` 不存在，doctor runtime_error=FileNotFoundError |
| 对齐器 | 配置缺失/未发现配置好的后端 | AlignmentProvider/CommandAligner 只有接口；未验证任意机器其他目录是否装有可复用模型 |
| workspace/临时目录 | 可读写，测试实际写入/原子更新通过 | 当前 workspace 的 os.access 检查通过；C 盘约 169.48 GiB、D 盘约 123.40 GiB 空闲，为本轮快照，不能据此推算未来容量 |
| Windows | 本地工程链路已验证 | 中文/空格、extended path、原子替换 sharing error、taskkill 进程树等有实际测试；MuseTalk native 扩展组合尚未验证 |

主包没有要求安装 DashScope/MiniMax 云 SDK，实际使用标准库 urllib。MuseTalk 应独立部署，不能直接往现有 Python 3.14 主环境塞入上游旧 Torch/MMCV 组合。RTX 5070 的 Blackwell 兼容性需按实际 CUDA kernel + mmcv.ops 导入确认；上游安装示例和硬件要求仅是参考。[MuseTalk 官方安装说明](https://github.com/TMElyralab/MuseTalk)、[PyTorch Blackwell 支持说明](https://pytorch.org/blog/pytorch-2-7/)。

## 5. 本轮实际验证记录

| 检查 | 结果 | 能证明什么 |
| --- | --- | --- |
| `python -m unittest discover -s tests -v`（默认 Python） | FAILED，12 个 test module 导入错误 | 默认解释器不能运行项目；不是 12 个业务测试逻辑失败 |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | **165 项，162 通过、3 跳过，59.197 秒，退出码 0** | 当前工作树工程回归通过；包含真实本地 FFmpeg 执行，AI/云/口型替身不能证明模型效果 |
| 3 个跳过项 | RealTTSIntegrationTests、RealLipSyncIntegrationTests、RealVideoIntegration | 显式付费/真实模型 opt-in 未设；跳过不能记为成功 |
| 通用 doctor / lipsync doctor | 通用工具可发现；MuseTalk ready=false | 本地可发现性及后端阻塞，不证明账号、模型质量或全集成 readiness |
| 3 个 TTS 示例 `make_provider` | 静态通过；对应 Key absent | Provider/model/endpoint 参数被本地校验接受，不涉及鉴权请求 |
| 31 个 Schema | Draft202012Validator.check_schema 全部通过 | Schema 定义有效，不代表所有真实响应符合 Schema |
| 文本/代码静态检查 | 扫描 src/tests/scripts/docs/examples/.agents 共 73 个 py、46 个 json、22 个 md、1 个 ps1；py AST 和 JSON 解析通过 | 结构检查；仓库根 README/AGENTS/pyproject/.gitignore 另外核对。未把构建缓存当权威实现 |
| 真实视频分析冒烟 | creator-001 一条真实片：720×1280、56.733333 秒，4 帧、2 候选镜头；约 0.97 秒 | 实际分析/采样能执行，不能证明场景/作者风格语义正确 |
| 真实离线 ASR 冒烟 | 前 8 秒 4 段/44 字；完整片经 MediaProcessor 10 段/100 字，约 4.355 秒；第二次 cache_reused=true、正文相同 | 实际 Provider/FFmpeg/缓存衔接能跑；准确率、漏识别需听验，未进行语音质量评分 |
| 源保护 | 上述真实片 SHA-256/mtime 未变；历史真实库 18 源当前 SHA 匹配 | 本轮已使用的真实输入未被改写；不代表所有入口都具备防误配置保护 |
| 现存 Skill | creator-001/v002、demo/v001 批准状态/包哈希/证据重核通过 | 本地包能读取；分别有 18 个真实 ASR 快照和 3 个合成 sidecar 快照，不能推导风格质量达标 |
| 人像审核 | 189 项审核模板，eligible=0 | 当前没有已通过人工审核的可用候选 |
| 路径/隔离问题复现 | CWD 改变后 MuseTalk 相对路径改变；TTS reference 可在源目录内写副本 | 确定性缺陷/脆弱配置条件；隔离复现用合成静音 WAV，无上传，无真实素材被写入 |

测试模块计数：workflow 25、quality 24、video_assets 19、tasks 17、tts 28、lip_sync 15、production 22、render 10、video_workflow 2，加 3 个 opt-in 集成测试。独立 scripts 的 real-suite、validate_v2_agent 等未执行：前者需凭据/模型，后者会调用真实 Agent；部分脚本会创建持久验收目录，本轮也未为“生成证据”擅自运行。

历史产物核对：V2 integration-summary 存在并记录 7 次调用，不能替代本轮请求或真实创作者盲评；V3-05 两份 validation-summary 标记 reference_audio_only=true；V3-06 三份 engineering-validation 明确 real_tts=false、real_lip_sync=false、style_quality=not_evaluated。未找到真实 portrait.json 或真实新 TTS speech.json，不声称从历史工程样片证明真实链路完成。

## 6. V3 全链路逐项判断

| 要求 | 代码存在 | 可执行事实 | 效果/验收状态 |
| --- | --- | --- | --- |
| 继承源参数 | 是：output_templates → profile.output → timeline.output | 按显示尺寸/旋转/SAR 归一化；生产默认继承宽高/方向/有理数帧率，render 用 CFR | 不是保留源码率/原容器/原编码；口型中间件固定 25fps，最后重采样到目标，真实质量未测 |
| 视频特征分析 | 是 | 实测参数/像素切镜/采样可跑 | 语义场景、动作、字幕 OCR/字体识别未自动接入；人工观察目前空 |
| 混合人物/B-roll/背景 | 是 | 前景/背景/ASS/音轨合成工程测试通过 | 新视频先对整段语音生成全部口型，再语义编排 B-roll；未按最终出镜区间减少推理，长片需大量候选；hook 不强制插入遮挡 |
| 保留作者视觉风格 | 部分 | 机位/动作可保留，导演接收 profile | 字体/颜色/位置仍是生产参数，未自动映射；静态图不生成身体动作，源标题/字幕/主题背景不会自动消除 |
| 防机械循环 | 是 | 动作组/源区间/使用次数/冷却/源帧不足检查 | 规则通过不证明新语义与原手势匹配；不兼容切口需人工修订 |
| 音画时间关系 | 是 | 主 PCM 不缩放；人像绑定音频哈希/偏移；B-roll 后返回人像按主时间位置 | 感知口型同步仍 pending |
| 字幕语音对齐 | 是，真实时间输入合同 | 支持服务商句级毫秒 + 分段偏移，或音频哈希绑定 timing；没有时间戳就拒绝估时 | 未有新 TTS 字幕实测；长句超过容量还需更细对齐 |
| 本地合成 | 是 | 真实 FFmpeg、CFR、H.264/AAC、ASS、混音、缓存与完整解码 | 工程通过；不是神经网络人物/TTS 效果通过 |
| 进度 | 是 | FFmpeg out_time、完成段/帧、模型事件；未知总量 percent=null；视频整体只报已完成阶段 | 非视频任务部分整体等权进度明确 estimated=true；不按等待时间模拟；远端请求精细进度不可得 |
| 错误/恢复 | 是 | task logs/attempts、停止子树、已完成产物哈希、分段 receipt、收费未知结果保护 | 强杀后锁需确认进程停止再处理；跨 workspace GPU 锁/全账户配额不统一；真实云恢复未验收 |
| 最终输出验证 | 是 | final_check 自动通过才发布 final.mp4；完整解码/音轨/时长/帧数/起点/黑屏/画幅/哈希 | 自动检查不能判定脸部稳定、声音自然、语义或视觉风格；合法黑色画面可能被黑屏策略拒绝 |

## 7. 问题分类、位置和下一步

### A. 配置缺失

| ID | 优先级 | 问题/实际证据 | 下一步 |
| --- | --- | --- | --- |
| A-01 | P0 | DASHSCOPE_API_KEY、MINIMAX_API_KEY、MINIMAX_CN_API_KEY 在 Process/User/Machine 都不存在；tts_providers.py:46 运行时才鉴权 | 先选一个 Provider、匹配账户/地域，安全设置真实变量，静态检查后另行受控真实测试 |
| A-02 | P0 | workspace creator-001/demo 的真实 voice asset 数均为 0 | 准备真实授权声音，reference → register 或核验现有云音色 import；保存实际 Voice ID/asset ID |
| A-03 | P0 | portrait-preflight/review.json 189 项、eligible=0 | 播放候选并完成审核/安全范围/action/pose；优先无旧字幕/旧主题包装的机位素材 |
| A-04 | P0 | examples/video/workflow.json 含 your-assets、占位音色/零哈希、authorized=false；real-suite 引用的 level1/2/3.txt 不存在 | 使用实际生成的 profile/assets/library/review/voice_asset 和授权；准备真实纯正文分级脚本，不覆盖示例或旧配置 |
| A-05 | P0（条件） | Qwen 无句级字幕，多句/长段会被 subtitles.py:112 拒绝；没有新声音 timing 或可用 alignment_command | Qwen 长稿必须配置真实对齐；MiniMax 可用真实服务商时间戳，但容量不足仍需细化；绑定当前 PCM 哈希 |
| A-06 | P1 | 已有 profile 的 manual observations=0、coverage=0；260 项 assets 初始 kind=video，rights 默认 unknown | 人工标注视觉证据，明确 production_config，分类/授权/标注新选题 B-roll/背景；不拿原声工程截图冒充成片素材验证 |

### B. 环境缺失/冲突

| ID | 优先级 | 问题/位置 | 下一步 |
| --- | --- | --- | --- |
| B-01 | P0 | MuseTalk repository/python 不存在，11 模型文件和 7 代码路径缺失，lip_sync_provider.py:40 返回 ready=false | 独立部署固定代码版本/完整模型；验证 Blackwell CUDA kernel 和 MMLab native 扩展；填绝对路径再 doctor |
| B-02 | P1 | 默认 Python 缺项目/jsonschema，测试无法导入 | 使用项目 .venv 的明确解释器或先激活；不以系统 Python 的失败推断功能失败 |
| B-03 | P1 | huggingface-hub/click 版本约束冲突，.venv 继承系统包 | 在后续环境维护中修正兼容版本或隔离主环境，再 pip check + 本地 ASR + 全测试；本轮未安装/升级 |

### C. 功能实现/集成问题

| ID | 优先级 | 位置和可复核依据 | 影响/解决顺序 |
| --- | --- | --- | --- |
| C-01 | P1 | voice_assets.py:26 add_reference 未校验 source/library input_folder 与 workspace 互不包含；先创建 references 目录再提取。隔离复现以素材目录本身作 workspace，成功在其下写 creators | 不覆盖源视频但会违反“源素材目录只读”。后续小修复复用现有 validate_input_folder，在创建目录/锁前拒绝包含关系；当前务必把 workspace 放在源目录之外 |
| C-02 | P1 | lip_sync.py:71 repository/python 使用 Path.resolve，相对进程 CWD；video_workflow.py:28 只把嵌套配置文件本身相对 workflow 解析 | 同一绝对 workflow 从另一目录启动会指向另一个模型目录，已复现。README 已说明该语义，但示例仍脆弱；当前填绝对路径可解决，后续若改变语义需兼容说明/测试 |
| C-03 | P1 | video_workflow.py:122 script_stage 只检查 Key 存在/口型环境；candidate 选择在 lip_stage 的 prepare_task，字幕检查在 production.py:88 load_timing，均晚于 tts | 模型就绪后仍可能先产生 TTS 费用，再因未审核候选/无对齐失败；Qwen 示例长稿尤其容易触发。后续增加有限前置检查，能提前确定的失败先阻断；实际音频时长/时间戳仍需生成后检查 |
| C-04 | P1 | portrait.py:133/145 产生 b_roll_or_scene_transition_required/b_roll_hooks；video_workflow.py:210 直接拼接口型段，:306 编排只注入 portrait-asset，未传递强制遮挡约束 | 不同 scene/pose 的切口可以进入最终人物连续轨；原 hook 保留在分段 portrait 记录，但导演/最终时间轴未强制消费。后续传递边界并要求 B-roll/合法转场或阻塞；当前逐切口人工审核并编辑 direction/timeline |
| C-05 | P1 | video_style.py:45/79 observations 为人工描述；production.py:18/84 默认配置只根据 output 缩放；timeline.py:18 仍默认 Microsoft YaHei/白字/底部 | 参考观察不会自动决定实际字幕字体/色彩/布局。这是源码明确的能力边界，不能宣称完整自动继承风格。当前手动填写 production_config；后续仅增加明确结构化映射，避免大重构 |
| C-06 | P1（条件） | tts_providers.py:211 fallback_mode → review_required；voice_assets.py:80 usable 只接受 ready/pending_activation；tts_cli.py:16 没有音色审核确认操作 | 降级音色会停住，没有同一资产“人工审核通过→可使用”的专门闭环。安全替代是改善参考、重新注册正常音色；后续补受审计确认状态变更，不能手改 JSON 或无审核绕过 |
| C-07 | P2 | cli.py:141 通用 doctor 仅 find_spec/which；独立 lipsync doctor 检后端，配置示例/工作流未做统一 readiness 汇总 | 所有基础字段看似存在仍不能知道 Voice/候选/字幕/素材是否就绪；doctor 返回 0 也不是端到端 ready。当前按清单逐项核验；后续增加只读诊断命令即可，不需改统一业务架构 |

附加能力边界（不计新的 C 模块）：图片输入只能口型不能身体动作；没有字幕擦除、OCR/字体识别、人物抠像、背景补全/搜索/生成、LatentSync 或远端口型适配器。这些不是“填 API Key”能开启的隐藏功能。现有业务模块都有 CLI/执行器/合同调用入口，未发现生产入口用 Mock 伪造模型成功；local_chunks/ffmpeg_demo 明确工程演示，测试 fixture 仅测试注入。多处 FFprobe/FFmpeg wrapper 和 config 校验存在重复，暂无本轮故障依据，合并可后置。部分 Protocol 方法显式 Unsupported，不能把声明方法当实现。未对整个代码库的所有不可达分支作形式化证明，不能断言绝无未使用代码。

### D. 验证缺失

| ID | 优先级 | 未完成项 | 验证要求 |
| --- | --- | --- | --- |
| D-01 | P1 | 当前 Codex 模型权限/新蒸馏/文案/真实创作者 V2 闭环、优化与对照 | 有额度和授权后运行受控少量请求，保留 execution/响应/Schema/证据；再用独立验收集盲评，不将 AI passed 当产品质量 |
| D-02 | P0 | 两个 Provider 的真实复刻、合成、字幕、激活和错误恢复 | 一次选一个，真实授权参考 + 实际 Voice ID + 新文案，FFprobe/完整解码/听验；按 receipt 和控制台核对账单/限流，不重置预算 |
| D-03 | P0 | MuseTalk 5–10 秒真人样片，身份/口部/手势/感知同步 | 环境 ready 后先短片，保留真实 metrics、源对照和人工评价；静态图不算动作驱动通过 |
| D-04 | P0 | 真实 V3 Level 1/2/3 | 新 TTS + 真模型依次 10–20、30–60、120–180 秒；前一级技术失败停止，分别做成片人工验收 |
| D-05 | P1 | 真实字幕时间/字体显示、B-roll 语义、作者视觉风格 | 听看新 PCM 与成片，记录 cue/时间误差、切口/布局/旧字幕干扰；人工评分可追溯到源证据 |
| D-06 | P2 | 新 wheel 安装复验、长真实视频 CPU/RAM 峰值/磁盘/真实预算 | 本轮使用 editable，不重建持久 wheel；后续独立环境安装当前构建，采集真实资源和账单，不以声明依赖/历史构建证明当前兼容 |

## 8. 配置管理与安全边界

统一视频工作流 JSON 已存在，TTS/口型/生产/评估仍使用各自配置，符合当前分层，用户通常不必改源码。examples/tts、examples/lipsync、examples/video 及 `production config`/`evaluation-config` 是等效模板。**没有 .env.example，也没有 load_dotenv；即使写了 .env，程序不会自动读取。** `.gitignore` 忽略 dotenv 只防提交，不是配置加载功能。

本轮在受审源码/示例中未发现真实硬编码 API Key；疑似长 token 正则命中是文档任务 ID，未展示任何值。端点/模型允许列表是明确支持边界，非秘密；示例 repository/python 是待填写相对路径。README 的特定运行 UUID/本机示例路径须替换，不是源码必须使用的硬编码机器地址。未扫描/导出私人 Codex 认证文件或 workspace 日志里的敏感内容，不能据此保证所有历史日志绝无秘密。

已实现明确的 MissingAPIKey/InvalidEndpoint/UnsupportedModel/VoiceNotReady 等错误、严格 JSON 字段校验、授权/哈希绑定、模型指纹、输出完整性检查。通用环境诊断不足和个别源路径防护缺口仍按 C 类处理。ASR small 默认按名称可能自动尝试下载，离线应指定现存本地目录；本轮没有下载权重。

## 9. 建议后续修复与验收顺序

1. 固定使用主 .venv；修正 click/huggingface-hub 冲突，确认 pip check 和全量 unittest。保持源码与数据只读边界。
2. 先处理 C-01 源目录隔离；所有模型/工具路径使用绝对路径。补全真实配置，不覆盖现有 examples/用户配置；只读核查 Voice/候选/对齐等前置条件，避免已知收费后失败。
3. 选一个 TTS Provider，配置匹配账户 Key、声音授权/参考、实际注册或导入音色。并行准备独立 MuseTalk 环境及完整模型；环境未 ready 前，不启动完整视频收费步骤。
4. 审核少量真实人像和干净机位素材，准备与新选题匹配的 B-roll/背景；填写视觉证据并明确 production_config 字体/位置/颜色。Qwen 路径必须先安排对齐器或可靠 timing 获取流程。
5. 先做一段真实新 TTS，再做 5–10 秒真人口型样片；人工听看通过后再修复/落实切口 hook，验证新声音字幕。
6. 依次执行真实 Level 1、2、3；每级读取 final_check、媒体参数/完整解码、错误/恢复、模型 metrics 和账单，再完成人物/声音/口型/语义/风格人工评价。
7. 最后做 V1/V2 的真实独立验收集和版本盲评、当前 wheel 独立安装复验；自动检查通过不代替人工发布确认。

具体参数、现存模板和命令步骤见 [CONFIGURATION_CHECKLIST.md](CONFIGURATION_CHECKLIST.md)。本轮没有进行上述安装、收费调用或代码修复。
