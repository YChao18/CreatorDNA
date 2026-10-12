# CreatorDNA V3.0

把同一创作者的参考素材文件夹，蒸馏成独立可复用的口播风格 Skill，再用新选题生成原创口播稿。

保留 V1.0 双层 Skill、V2.0 质量闭环与 V3.0-01 至 05 的素材、任务、TTS、口型和编排接口。V3.0-06 将这些模块接入统一任务，新增本地 FFmpeg 分段合成、原生进度、缓存复用、成品检查和 MP4 输出。文案流程沿用 Codex CLI，模型运行在独立 Python/CUDA 环境，凭证只从环境变量读取，无数据库服务或 Web 平台。

V3.0-05 的 Video Style Profile、Video Director Skill、授权素材、真实时间字幕与可编辑时间轴继续复用。输出默认继承模板比例、方向和有理数帧率。**当前状态（2026-10-12）：V3 开发完成，案例视频生成成功，未优化。** 本机已运行真实 TTS 音频、MuseTalk 动态人像口型、人物背景处理、字幕编排与 FFmpeg 成片链路；人工质量评价及完整 Level 1–3 验收仍未完成。阶段历史见 [V3.0-06 验收记录](docs/V3-06-VALIDATION.md)，模块划分见 [完整 V3 架构](docs/V3-ARCHITECTURE.md)。

## 当前案例与完成范围

2026-10-12 完成 creator-001 的“AI 工厂 / 老设备”动态人像案例。下表来自已保存任务、人物来源记录和成品验证文件；本次文档更新未重新调用 TTS 或口型模型。

| 项目 | 实际结果 |
| --- | --- |
| 任务 | `task-564d83eeb90a40eb85fec2c2bb7cbdff`，状态 `completed` |
| 本地案例文件 | `workspace/creators/creator-001/outputs/AI工厂_老设备_原片动态人物_20261012.mp4` |
| 视频规格 | 32.28 秒，720×1280，9:16，25fps，807 帧，H.264 / AAC |
| 人物来源 | `materials/creator-001/raw_portraits/rough_cut.mp4`；整片锁定同一源，4 段按连续源时间推进 |
| 口型与身体 | MuseTalk 使用对应口播音频驱动嘴部；处理记录注明保留原身体/手势序列，未生成新身体动作 |
| 人物处理 | 本地绿幕 alpha 处理，输出 800×1000 上半身透明工作素材，再合成为竖屏视频 |
| 自动检查 | `automatic_checks=passed`、`decode_ok=true`、无报告的黑屏区间 |
| 人工评价 | `human_review=pending`；尚未完成风格、声音、口型、手势和抠像效果验收 |

任务证据位于 `workspace/tasks/task-564d83eeb90a40eb85fec2c2bb7cbdff/`：`status.json` 保存阶段状态，`stages/lip_sync/attempt-001/outputs/portrait-asset.json` 和 `portrait-clean.processing.json` 保存源锁、分段和处理记录，`stages/final_check/attempt-001/outputs/verification.json` 保存成品技术检查。案例副本 SHA-256 与任务成品一致：`16a2ca77831690804bc94f0dc8d25dba331be3d793eb3aba50dd2f480da8e2c5`。

本案例复用此前真实合成的音频，TTS 阶段记录 `reused=true`，不是本轮重新付费合成。单个 32 秒案例也不等于 Level 1–3 顺序验收全部通过。“未优化”表示仍需结合实际观看结果完善声音、口型、动作匹配、人物边缘、字幕、B-roll 和整体风格；自动检查只确认技术条件。

`materials/`、`workspace/`、模型环境及 `.env` 均不提交 Git。GitHub 保存代码、文档和配置模板；克隆仓库后需自行准备原片、授权、音色、模型与运行配置，案例 MP4 和私人运行记录仍保存在本地。

## V3.0-06：统一视频入口

人像候选默认自动选择，不再要求人工审核确认。省略 `portrait.review` 或独立 `lipsync generate --review` 时，以实际采样中单人脸、足够时长的候选排序，并标记 automatic_candidate；不会伪造 reviewer、人工质量分、身份、嘴部无遮挡或姿态判断。授权、源哈希、重复区间/使用账本、模型逐帧定位和技术检查仍保留。需要保留已提供的人工筛选结果时可继续传 review；默认自动流程不会等待审核输入。成片效果评价与候选准入分开。

本机已部署真实 MuseTalk 环境，配置为 `workspace/models/musetalk.json`，先完成 8 秒口型接入样片，后完成上面的 32.28 秒动态原片案例。安装版本、兼容修复和早期 GPU 指标见 [MuseTalk部署记录](docs/MUSETALK-SETUP.md)。该部署记录中的旧剪辑素材样片仅为历史接入证据；新视频必须使用 `raw_portraits` 动态原片。

完整新文案视频需要准备匹配选题的标题/背景、为新 PCM 提供真实细时间字幕，并按需从动态原片口型结果中提取完整可见上半身。工作流可配置 `portrait_processing_command`（argv 列表，`{request}` 替换为请求文件）：请求包含 input/input_sha256/output/duration/frame_rate/segments/audio_sha256/source_lock/required_framing，处理器须在指定 output 写入可播放人像视频及同名 `.processing.json`，记录上半身构图、源锁和输入/输出哈希。透明背景可输出 ProRes4444 MOV；成功退出但缺文件、缺构图记录、输入被修改或时长不符时会停止。人物素材仍绑定整段主 PCM 哈希。背景和 B-roll 默认覆盖整屏，人物 layout.rect 只控制人物位置，防止标题/背景被裁成人物小窗。每个口型段最多允许一个正常 CFR 端点帧延展来对齐小数时长，不允许循环片段补长。

### 每个素材文件夹的默认音色

在项目根目录 `.env` 填写 `MINIMAX_CN_API_KEY=实际密钥`。CLI 启动时自动加载当前目录或上级目录中最近的 `.env`，已有进程环境变量优先；worker 继承环境，密钥不写入 JSON 或任务快照。`.env` 已被 Git 忽略。安装项目会同时安装 python-dotenv；不需要把密钥写到 TTS 配置。

每个创作者绑定自己的只读素材文件夹和已有云音色。以下命令只查询云音色并保存本地绑定，不上传参考、不复刻、不合成音频：

```powershell
.\.venv\Scripts\python.exe -m creator_dna tts bind --workspace workspace --creator creator-001 --input materials/creator-001 --config examples/tts/minimax-cn.json --voice-id QianHao_GPT --authorize --cloud-processing --confirmation "填写真实音色使用授权、确认该文件夹对应此音色及 MiniMax 云端合成处理范围"
.\.venv\Scripts\python.exe -m creator_dna tts binding --workspace workspace --creator creator-001
```

绑定保存在 `workspace/creators/<creator>/voice-assets/default.json`，包含实际素材路径、云端 voice_id、本地 voice_asset、Provider/端点/模型。原素材目录保持只读。其他素材文件夹使用自己的 creator ID 和音色重复绑定；同一个文件夹不能绑定到不同创作者。若当前 manifest 指向别的文件夹，先核对 creator/workspace，不会自动串用音色。

后续合成可省略 `--voice`，自动使用该创作者的默认绑定。下面不带 `--run` 时只创建任务，带 `--run` 才产生真实合成调用：

```powershell
.\.venv\Scripts\python.exe -m creator_dna tts synthesize --workspace workspace --creator creator-001 --config examples/tts/minimax-cn.json --script-file my-script.txt
```

`video create` 的工作流保留 `tts` 配置，可省略 `voice_asset` 使用默认绑定；仍需按其他说明准备人像模型、授权、字幕及素材。任务创建时固定实际音色，之后改默认绑定不会改变已排队任务。未绑定、Provider/账户或模型不匹配时明确报错；显式 `--voice` 或工作流 `voice_asset` 仍可用于单次覆盖。换电脑需迁移私人 `.env` 和 workspace，在新素材位置重新绑定；云端音色需要同一账户且实际存在。文本 `generate` 不会自动变成收费 TTS。

先激活项目运行环境。系统默认 Python 可能没有项目依赖；FFmpeg/FFprobe 必须可用，图片需要 Pillow。新主题需要已批准 Creator Skill、新声音需要已授权且已注册音色，真实口型需要独立 MuseTalk 环境与权重。

```powershell
& .\.venv\Scripts\Activate.ps1
python -m creator_dna video create --workspace workspace --creator creator-001 --config examples/video/workflow.json --topic "全新选题" --run
python -m creator_dna video create --workspace workspace --creator creator-001 --config examples/video/workflow.json --script-file my-script.txt --run
python -m creator_dna video render --workspace workspace --creator creator-001 --timeline path/to/timeline.json --render-config examples/video/render.json --run
```

`--script-file` 是纯口播正文，不含标题、Markdown 附注或分镜标签。`create` 省略 `--run` 只创建任务，返回 `task_id`。`render` 先在新运行重建编辑后的 ASS/时间轴，再渲染，不调用文案 Agent、TTS 或口型模型。

```powershell
python -m creator_dna tasks run --workspace workspace --task task-替换为真实UUID
python -m creator_dna tasks status --workspace workspace --task task-替换为真实UUID --watch
python -m creator_dna tasks logs --workspace workspace --task task-替换为真实UUID --tail 100
python -m creator_dna tasks cancel --workspace workspace --task task-替换为真实UUID
python -m creator_dna tasks run --workspace workspace --task task-替换为真实UUID --resume
python -m creator_dna video clean --workspace workspace --task task-替换为真实UUID
```

输出状态包括开始时间、已运行时间、当前阶段/步骤、真实段数或帧数、FFmpeg `out_time`、失败原因、日志和成果路径。不同子操作的分母只适用于对应子操作；运行中的视频任务不显示虚构的整体百分比。成功结果的 `stages[final_check].result.video` 指向 `final.mp4`，相邻 `verification.json` 与 `human-review.md` 分别记录自动检查和待完成的人工评价。Windows 深层任务文件使用 `\\?\` 原生路径前缀，避免 260 字符限制。

配置示例 [examples/video/workflow.json](examples/video/workflow.json) 是待填写模板，授权默认 false。顶层媒体/配置文件路径相对于工作流 JSON；引用的 TTS/口型配置保留各模块原有语义，MuseTalk 配置里的相对 repository/python 路径仍相对于执行目录，建议使用绝对路径。`profile` 与 `assets` 使用 V3.0-05 产物；`production_config` 可指定字幕安全区、布局和背景，默认继承 profile 的输出。`quality=true` 接入 V2 有限修订，未通过会在收费前停止；`max_revisions` 默认 2。`director=agent` 调用既有 Video Director，`local` 只做明确标记的字面标签匹配。

`portrait_authorization` 明确授权 `selected_portraits_with_workflow_speech` 的本地处理范围，列出真实源 SHA256、创作者、确认意见与时间。工作流在新声音落盘后将该声明绑定实际 PCM 哈希并保存 `authorization.json`，不自动扩大其他授权。5–10 秒的口型分段继续使用候选检查、预留账本与去重复规则；单一原片不足会失败，不用第二份源、静态图或循环人像补长度。新视频必须遵守下方的动态原片与整片单源约束。

已有声音可以配置 `speech`（V3.0-03 speech.json），省略 tts/voice_asset；已有对应人像可省略 lipsync/portrait/portrait_authorization，此时 assets 的 portrait 必须绑定同一 PCM 哈希。没有真实句/词时间戳时，配置 `timing` 标准文件，或 `alignment_command` 调用已有外部对齐器，例如 `["aligner.exe", "--request", "{request}", "--output", "{output}"]`。请求 JSON 提供 audio、audio_sha256、text、output；输出必须符合现有 subtitle-timing Schema。没有对齐器的千问长稿可能在 composition 阶段停止；不会按字数虚构时间。MiniMax 配置已开启服务商字幕，但仍以实际返回结果为准。

渲染支持背景/前景、B-roll、含 alpha 素材、EXIF/视频方向、正方形像素归一化、contain/cover、裁切、anchor、ASS 字幕/高亮、cut/从 pad_color 淡入、CFR、H.264/AAC MP4。所有视觉源音轨静音，主口播单独映射；`render.audio_mix` 可加入已授权音乐/音效音轨。每轨需 path、sha256、start、end、source_start、volume（0–2）、rights 和 rights_note，时间不能越出主时间轴，源不足不会循环。混音后有峰值限制；音轨变更仅重新混音，复用画面缓存。

已完成任务重复 run 是幂等读取；失败/取消后显式 resume 校验并复用完成阶段。TTS 仍保存每次收费请求的 receipt 和预算，未知结果禁止重新 POST；核对账单后使用既有 `tts resolve` 对具体段确认，视频任务也支持此入口。修改字幕、背景或素材后应创建新的 `video render` 任务，已停止旧任务保持不变，未受影响画面按内容哈希复用。每次输出位于独立 attempt 目录；FFmpeg 使用 `-n`，检查通过后才发布 final.mp4，不覆盖成片。`video clean` 只删停止任务的渲染临时文件；加 `--cache` 在无运行任务时清理共享画面缓存，保留声音凭据、口型分段和阶段成果。强杀进程后若残留锁，先确认相关进程均已停止，再按已有 Task Manager 规则处理 .lock。

### 工程与真实验证

```powershell
python -m unittest discover -s tests -v
python scripts/validate_v3_render.py --engineering --workspace workspace/v3-06 --duration 15
python scripts/validate_v3_render.py --real-suite examples/video/real-suite.json --workspace workspace/v3-real
```

工程模式明确生成测试图、正弦音和合成字幕；可测试 45/150 秒的本地长片稳定性，不能算真实 Level 1–3。真实套件按 Level 1（10–20 秒）、2（30–60 秒）、3（120–180 秒）顺序运行，每级必须新 TTS + 真实口型，上一级自动检查或实测时长失败后停止更长级。准备匹配长度的 level1/2/3.txt 并修改示例套件；不按字数声称音频时长。可选 unittest 入口：设置 `CREATORDNA_VIDEO_REAL_TEST=1`、`CREATORDNA_VIDEO_WORKSPACE` 与 `CREATORDNA_VIDEO_SUITE`。真实脚本记录任务 ID、时长、字节数和耗时；请求凭据和 GPU metrics 保留在任务中，真实计费金额需服务商账单，CPU/RAM 峰值尚无统一采集。自动通过仍需人工观看人物、声音、口型、动作、B-roll、字幕及整体风格。

## V3.0-05：字幕、场景和 B-roll 编排

需要现有 FFmpeg/FFprobe；图片素材需要 Pillow（已有 video 可选依赖）。下面返回的 output 路径均在指定 workspace 的新运行目录中，旧文件和源素材保持只读。命令中的占位路径必须换成实际文件。

```powershell
$workspacePath = "workspace/v3-05"
$libraryPath = "workspace/v3-04/creators/creator-001/runs/video-analysis-184e5d0c3be74773bb6cd557512e8478/library.json"
.\.venv\Scripts\python.exe -m creator_dna production profile --workspace $workspacePath --creator creator-001 --library $libraryPath
.\.venv\Scripts\python.exe -m creator_dna production assets --workspace $workspacePath --creator creator-001 --library $libraryPath
```

profile 默认选按时长推荐的模板，也可 `--template template-002`。记录所选模板的输出、候选镜头持续时间分布、人工标注出镜占比与未知覆盖率。字幕位置/字体/颜色/描边/阴影、关键词习惯、转场、背景、开头/结尾包装及构图没有可靠证据时保留 unknown；人脸检出率不会被换算为 B-roll 占比。生成的 review-template.json 可填写 reviewer、按 source_id 的非重叠时间区间及观察证据，再用 `profile --review <审核文件>` 生成新 profile。引用时间与帧 ID 必须在对应源视频范围，源哈希必须与 V3.0-01 一致。人工审核不是自动风格识别；生产默认样式也不是作者习惯。

assets 从 V3.0-01 导入测量候选，使用权初始均为 unknown。编辑资产清单补充明确的 kind、source、rights、rights_note、topics、description 和可用源区间；未授权候选不会被选入。支持图片、短视频、产品图/视频、截图、背景及已有口型成品。owned/licensed/authorized 可选，unknown/denied 不可选。素材清单授权说明由使用者负责如实填写；不抓取第三方素材，不联网搜索或生成图片。

导入已有授权补充素材会实测媒体、记录哈希，返回新的 assets.json，不覆盖传入清单：

```powershell
$registryPath = "<上一步 assets.json 的路径>"
.\.venv\Scripts\python.exe -m creator_dna production import --workspace $workspacePath --creator creator-001 --registry $registryPath --asset D:\Authorized\robot.png --id robot-product --kind product --media-type image --source "公司产品图库" --rights owned --rights-note "本公司素材，可用于本项目视频" --topic "工业机器人" --description "已核验的工业机器人产品实拍"
```

每次 import 后使用返回的新清单。人像轨只能使用已有视频，且绑定主口播 PCM 音频哈希。V3.0-04 分段成品可通过 `--audio-offset` 说明首个可用源帧对应整段主音频的位置，不能拿原视频替代新文案口型成品：

```powershell
.\.venv\Scripts\python.exe -m creator_dna production import --workspace $workspacePath --creator creator-001 --registry $registryPath --asset "<V3.0-04 成品 portrait.mp4>" --id portrait-001 --kind portrait --media-type video --portrait-audio "<V3.0-03 整段 timeline.wav>" --audio-offset 0 --source "本项目已审核口型成品" --rights authorized --rights-note "本地制作授权及审核依据" --description "对应当前主音频 0 秒开始的人像镜头"
```

口型成品的内嵌音轨在编排中静音，只用统一主音频。B-roll 遮挡期间不压缩人物音频位置，回到人像时使用对应主音频秒位置；同一人像源区间不能重复使用。人像不足会保留 null 镜头与 missing_assets，ready_for_render=false，不循环凑时长。

```powershell
$profilePath = "<profile 返回的 video-style-profile.json>"
.\.venv\Scripts\python.exe -m creator_dna production config --workspace $workspacePath --creator creator-001 --profile $profilePath
.\.venv\Scripts\python.exe -m creator_dna production plan --workspace $workspacePath --creator creator-001 --profile $profilePath --registry "<最新 assets.json>" --speech "<V3.0-03 speech.json>"
```

plan 默认复用 CodexAdapter，分析正文/语义重点和素材说明，仅发送文本与结构化元数据，不上传音视频。模型沿用 CLI 配置，可用 `--model` 指定本机可用模型。Agent 只输出连续 cue_ids、镜头角色、选择理由、关键词与布局；时间和路径由确定性模块绑定，输出须通过 Schema 与引用检查。独立 Skill 位于 `.agents/skills/creator-dna-video-director/`，整个目录可复制复用，无需安装本 Python 包或读取原素材。

`--director local` 不调用 Agent，只根据口播中出现的素材标签进行字面匹配，不能当作语义理解或作者风格复现。`--direction-file <video-direction.json>` 可直接使用人工/外部 Agent 编排。每个 cue_id 必须按顺序恰好覆盖一次；asset_policy=selected 需明确已有 ID，auto 由标签匹配，missing 明确保留素材需求并禁止自动选回不适用的同主题素材。B-roll 缺失可回退口播并记录非阻塞缺失项；回退人像也不足时阻塞渲染。选择结果保留 reason、授权依据及实际素材源区间。

字幕优先用 speech.json 的服务商句级毫秒时间戳，叠加实际分段起点和 PCM 停顿。无服务商时间戳时，只接受容量内的单句实测分段边界并标记 coarse；长段落、多句或过长单个时间区间必须提供更细对齐结果，不按字数均摊时间。canonical_audio 实测内容会与分段 PCM/插入静音核验，以避免旧清单未哈希 canonical_audio 的错配。

`--timing` 导入绑定当前 canonical PCM 哈希的对齐文件；Python 的 AlignmentProvider 可接本地强制对齐器。本阶段不内置或下载新对齐模型。标准格式见 `src/creator_dna/schemas/subtitle-timing.json`，时间单位秒：

```json
{
  "schema_version": "3.0-05",
  "audio_sha256": "<当前 PCM WAV 的 64 位 SHA256>",
  "text": "实际口播正文。",
  "method": "forced_alignment",
  "provenance": "所用本地对齐器、版本及核验说明",
  "cues": [{"cue_id": "sentence-0", "start": 0.12, "end": 2.48, "text": "实际口播正文。", "timing_basis": "实际音频强制对齐"}]
}
```

这只是格式示例，不是实际时长证据。method 可为 forced_alignment/provider/asr/asr_reviewed；普通 ASR 结果应如实标 asr，人工确认后才标 reviewed。音频哈希、正文覆盖、时间范围、顺序与重叠均须一致。词级/更细时间戳可按标点和字幕容量组合，但不在原时间单元内部猜切点。已有外部 PCM WAV 可用 `--audio <wav> --timing <json>`；不要求 TTS 的固定服务商来源。

生产配置可通过 `--config <config.json>` 修改字幕 font/font_size/color/highlight_color/outline_color/outline/shadow、top/center/bottom、margin_x/margin_y、max_chars_per_line/max_lines。默认白字、黄色关键词、黑描边；字号/边距按参考画幅缩放，字符容量必须位于安全区。ASS 支持中英文、标点、固定换行及静态关键词高亮；时间精度为厘秒，无法表示的零时长/重叠会报错。字幕文本与口播正文忽略大小写、全半角、空白和标点后比较，不会允许换词。

layout 使用 0–1 的目标 rect 与可选源 crop、anchor=center/left/right、fit=contain/cover 和 pad_color。contain 保持比例并补边，cover 按锚点等比裁切；禁止 stretch。背景轨在人物/辅助镜头下方，字幕位于上方。cut 的 duration 为 0；fade 是片段自身起始区间内淡入，不代表重叠交叉溶解。布局执行规范见 [时间轴合同](docs/V3-05-DESIGN.md)。

输出包括 timeline.json、subtitles.ass、验证报告、编排/配置快照、主音频及所选素材的哈希验证副本。时间轴素材路径相对其目录，可直接搬移整个运行目录交给下一阶段本地渲染器；不发布生产级 MP4。可编辑 timeline.json 后校验/重新生成新运行：

```powershell
.\.venv\Scripts\python.exe -m creator_dna production validate --timeline "<timeline.json>"
.\.venv\Scripts\python.exe -m creator_dna production rebuild --workspace $workspacePath --creator creator-001 --timeline "<编辑后的 timeline.json>"
```

validate 检查 Schema、素材存在与哈希、实测媒体范围、授权、音频、人像偏移、非法时间、同轨重叠、前景缺口、字幕覆盖、安全区、ASS 与配置一致性、非法转场及非有限数。跨背景/前景/字幕轨的重叠是合法叠加。改字幕/输出配置后 validate 提示 rebuild；重建保留旧文件，重新生成 ASS 和复制资产。若缺素材，先补充资产/重做 plan，或编辑完整时间轴素材与片段再 rebuild。退出码：成功为 0；错误、invalid、needs_assets 为 1；可渲染但存在非阻塞 B-roll 缺项时仍为 0，必须同时检查 missing_assets 与 ready_for_render。

真实新口播验收入口（需已有生成音频和已授权人像/素材，不会调用 TTS）：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/validate_v3_production.py --workspace $workspacePath --creator creator-001 --library $libraryPath --speech "<真实 speech.json>" --registry "<授权 assets.json>" --director codex
```

需要更细时间戳时增加 `--timing`。V3.0-05 开发阶段保留的 9.32 秒参考原声样例、ASR 初始时间戳、编排与 ASS 仅用于工程检查；当前真实案例见本文开头。普通 unittest 使用明确标注的合成素材，不调用付费 TTS/口型或真实 Agent。

### 整片人物来源与上半身构图约束

人像参考原片放在 `materials/<creator>/raw_portraits/`。本项目当前目录为 `materials/creator-001/raw_portraits/`，由用户手动加入未经剪辑、没有烧录字幕/标题、上半身清楚可见的动态视频。根目录的已剪辑视频可继续用于表达风格和视频风格分析，不再用于新视频的人像生成。整片仍统一一个源文件，保留原身体/手势动作并使用新音频驱动嘴部；不得用静态截图或循环动作替代。

该目录为空、被删除或旧素材库未索引到原片时，人像选片会明确报错；完整工作流在 TTS 前检查来源，不回退到旧剪辑片。旧截图/剪辑片生成的人像也不能在新工作流中直接复用。目录约定约束来源，不等于已经自动证明素材无烧录文字、构图和输出质量通过；加入原片后仍按实际采样/执行结果检查，生成前不增加人工候选审批门槛。

加入原片后，重新分析专用目录，把返回的新 `library.json` 填入工作流的 `portrait.library`（已有 style profile、音色绑定和文案素材不用迁移）：

```powershell
.\.venv\Scripts\python.exe -m creator_dna analyze-video --input materials/creator-001/raw_portraits --workspace workspace --creator creator-001
```

不要沿用此前指向剪辑素材的 `portrait.source_id` / `portrait.candidate`；省略以自动选择新原片，或换成新原片库中实际的 ID。原片仍留在此目录，分析、抠像、口型、字幕与输出写入 workspace。小范围新身体动作仅在有真实可用的动作生成能力时使用并记录，目前 MuseTalk 本身只生成口型，不生成新手势。

4K 原片可显式配置 `portrait.reference_max_dimension=1920`，或在 `lipsync generate` 使用 `--reference-max-dimension 1920`，只将模型工作副本等比缩到最长边 1920；原片、来源哈希和源时间区间不变。默认不缩放，可设置范围为 512–8192。人物仍完整放入画面，不裁成头像；最终视频尺寸继续由 style profile / production_config 决定。

整片 `video create` 在推理前保存 `checkpoints/portrait-plan.json`：一个源文件路径、SHA256、各段源区间和音频边界。恢复逐段比对原计划及真实生成清单，旧任务若没有单源计划须创建新任务。已完成视频保留，不覆盖。复用人像的素材清单必须包含 `portrait_provenance`；缺失记录的旧合并视频不会自动标记为单源合格。

人像合成固定使用 `contain`、无二次裁切；透明留白保留新背景。直接编辑时间轴裁人像或混入另一个来源会被校验拒绝。完整上半身必须在原片中可见，模型只改嘴部，不生成新身体动作；源片存在换衣服、其他人物、遮挡或缺失身体的区间不能靠同文件规则自动保证效果。

`portrait_processing_command` 可调用本地上半身处理器，命令参数用 `{request}` 接收输入。新内置桥接脚本 `src/creator_dna/portrait_matting_runner.py` 在独立模型 Python 中执行 RVM：

```json
["<模型环境 python.exe>", "<项目>/src/creator_dna/portrait_matting_runner.py", "<workspace>/processor.json", "{request}"]
```

`processor.json` 填 `repository`（RVM checkout 与 rvm_mobilenetv3.pth）、`source_sha256`（选定原片）、`upper_body_roi`（原视频像素坐标 x1,y1,x2,y2）、`face_anchor`（实际人脸 x,y,w,h）与可选 `downsample_ratio`。ROI 需包含头、肩、躯干至腰部和画面中可见手势，不能用脸颈裁切躲字幕。原片必须无烧录字幕/标题；文字、遮挡或缺失上半身导致素材不适合时停止并补充原片。处理器必须输出同名 `.processing.json`，记录 upper_body、源锁、输入和输出哈希；缺记录、脸颈旧处理器或失败返回不发布人像资产。构图记录和技术解码不等于口型感知/抠像质量通过。

## V3.0-04：新口播音频 → 短人像口型视频

能力、依赖和权重检查不会上传媒体或自动下载模型：

```powershell
.\.venv\Scripts\python.exe -m creator_dna lipsync capabilities --config examples/lipsync/musetalk.json
.\.venv\Scripts\python.exe -m creator_dna lipsync doctor --config examples/lipsync/musetalk.json
```

先按 [实现与后端选择](docs/V3-04-DESIGN.md)准备独立 MuseTalk checkout、Python/CUDA 环境和模型权重；修改 `examples/lipsync/musetalk.json` 中的 repository/python 路径。doctor 检查真实 CUDA kernel、MMLab 扩展和文件，ready 不是口型质量通过证明。本机 RTX 5070 约 12 GB，已完成环境部署和真实推理；[阶段验证报告](docs/V3-04-VALIDATION.md)保留开发阶段的历史状态，当前进展见本文案例及 [部署记录](docs/MUSETALK-SETUP.md)。

使用对 `materials/creator-001/raw_portraits/` 运行 analyze-video 得到的人像清单；将下面的 workspace 改为该清单实际使用的目录，不使用旧剪辑视频的分析库：

```powershell
$workspacePath = "workspace/v3-04"
$creatorRoot = Join-Path $workspacePath "creators/creator-001"
$pointer = Get-Content (Join-Path $creatorRoot "video-assets/latest.json") -Raw | ConvertFrom-Json
$libraryPath = Join-Path $creatorRoot $pointer.library
.\.venv\Scripts\python.exe -m creator_dna lipsync rank --workspace $workspacePath --creator creator-001 --library $libraryPath
```

默认自动筛选有单人脸采样且时长足够的候选，不需要填写审核文件。自动结果保留 quality_score=null、review=null、姿态 unknown，不把采样指标当作身份、嘴部无遮挡或动作语义判断；模型逐帧定位失败会停止。已有人工筛选记录仍可通过可选 --review 导入；review-template 只用于这条可选路径。自动口型模型不会擦除源文字，因此必须准备无烧录字幕/标题的动态原片。

```powershell
.\.venv\Scripts\python.exe -m creator_dna lipsync plan --workspace $workspacePath --creator creator-001 --library $libraryPath --durations 6 8 --timeline new-topic-001
```

plan 不生成视频、不占用候选；实际任务运行时再次核验并预留。同一 timeline 只能锁定一个源文件；单源不足报 InsufficientSingleSourcePortraitMaterial，不能用第二个文件补时长。`lipsync plan/generate --source-id <library 中真实 source_id>` 可显式指定；完整工作流对应 `portrait.source_id`。自动测量中同一镜头内连续、合格的候选可形成 `-window` 候选，按源时间顺序分段、不循环；同文件不自动证明同身份/服装，原片仍需检查。默认同一 timeline 中片段/重叠源区间/相同动作最多用一次；可设置 --max-uses，但仍检查 --cooldown（默认 30 秒）。切镜不匹配返回 b_roll_hooks，供后续编排遮挡。素材不足直接失败，不循环、倒放或随机拼接。

当 V3.0-03 生成新音频后，创建与具体源视频和音频 SHA256 绑定的**本地**授权。以下路径须换成真实文件：

```powershell
$ttsManifest = "workspace/你的TTS输出目录/speech.json"
$portraitSource = "materials/creator-001/raw_portraits/已授权的动态原片.mp4"
$consentPath = Join-Path $workspacePath "portrait-consent.json"
.\.venv\Scripts\python.exe -m creator_dna lipsync consent --workspace $workspacePath --creator creator-001 --source $portraitSource --tts-manifest $ttsManifest --destination $consentPath --authorize --confirmation "已获创作者授权：人像重定向与新音频使用，仅限本地"
.\.venv\Scripts\python.exe -m creator_dna lipsync generate --workspace $workspacePath --creator creator-001 --config examples/lipsync/musetalk.json --library $libraryPath --tts-manifest $ttsManifest --consent $consentPath --duration 8 --timeline new-topic-001 --run
```

也可用 `--audio 新口播.wav` 代替 `--tts-manifest`；`--audio-start` 选择新音频中的起点。`--candidate` 固定候选；多候选授权可在 consent 的 --source 后给多个源路径，实际整片仍只能使用一个源文件。每段只允许 5–10 秒，并要求候选多留 0.2 秒源帧余量。底层保留图像口型接口，但本项目新视频禁用静态截图替代；已绑定原片目录的创作者会拒绝图像参考。

generate 不带 --run 只创建任务；执行使用现有任务引擎：

```powershell
.\.venv\Scripts\python.exe -m creator_dna tasks run --workspace $workspacePath --task task-实际ID
.\.venv\Scripts\python.exe -m creator_dna tasks status --workspace $workspacePath --task task-实际ID
.\.venv\Scripts\python.exe -m creator_dna tasks run --workspace $workspacePath --task task-实际ID --resume
```

进度显示实际子操作的帧数/媒体秒；模型加载和人脸定位总量未知时不伪造百分比。耗时由任务引擎持续记录；失败保留 environment.json 和模型日志。重试重新验证输入与模型哈希，task_id 幂等复用预留，已完成任务不会重新推理。失败/中断预留保留在 `creators/<creator>/portrait-assets/usage.json`；缺源、错误授权、模型变化不会作为可自动重试错误。

成功输出 `portrait.mp4`、`portrait.json`、对照 reference/speech 和人工检查表。只在完整解码、画幅、帧数、时长及音视频时间戳检查通过后发布任务成品；human_review 始终先为 pending。必须观看口型、脸部/牙齿稳定、身份、手势与语义、动作保留、切镜/光影和感知同步，不把工程 tests 当真实质量证据。本适配器只支持本地，无第三方人像/声音上传。

可重复执行的本地 preflight 与显式真实推理入口：

```powershell
.\.venv\Scripts\python.exe scripts/validate_v3_lip_sync.py --workspace $workspacePath --creator creator-001 --library $libraryPath --config examples/lipsync/musetalk.json
# 准备好新音频、审核和授权后，在同一命令后增加：
# --review $reviewPath --tts-manifest $ttsManifest --consent $consentPath --timeline validation-001 --run
```

真实模型 unittest 默认 skip；显式设置 `CREATORDNA_LIPSYNC_REAL_TEST=1` 与 CONFIG/WORKSPACE/CREATOR/AUDIO/LIBRARY/REVIEW/CONSENT/TIMELINE（均加 `CREATORDNA_LIPSYNC_` 前缀）才执行。可选 CANDIDATE/DURATION，默认 8 秒。这个测试只证明模型推理及技术输出，人工质量仍待验收。普通 `python -m unittest discover -s tests -v` 不调用真实模型或云 API。

## V3.0-03：授权声音 → 全新口播音频

需要 FFmpeg / FFprobe；不需要新增 Python 云 SDK。配置示例在 `examples/tts/qwen.json`、`examples/tts/minimax.json`（国际）和 `examples/tts/minimax-cn.json`（国内当前官方端点）。官方模型、接口、限制、生命周期及实现边界见 [TTS 接入审计](docs/tts-api.md)。API Key 只设置为 `DASHSCOPE_API_KEY` / `MINIMAX_API_KEY` / `MINIMAX_CN_API_KEY` 环境变量；不要写入 JSON、命令参数、授权说明或日志。

先检查能力（无 Key、无网络、无上传）：

```powershell
.\.venv\Scripts\python.exe -m creator_dna tts capabilities --config .\examples\tts\qwen.json
.\.venv\Scripts\python.exe -m creator_dna tts capabilities --config .\examples\tts\minimax.json
```

千问目前适配 `qwen3-tts-vc-2026-01-22`，音色严格绑定该模型；数值语速保持 1.0、emotion=null、subtitles=false，其他值会明确返回 Unsupported。MiniMax 支持配置语速 0.5–2、按模型限定情绪和原始句级字幕。`format` 支持 wav/mp3/flac；千问原生 WAV 经本地转码，MiniMax 请求对应格式；`sample_rate` 是成品采样率。每段最多服务商实际字符上限，默认 500；段落之间插入实测 PCM 静音，`paragraph_pause` 默认 0.35 秒。

每个真实参考都需要明确声音使用/复刻授权，并确认**选定服务商、账户端点**处理参考声音及文案。下面的 flags 和 confirmation 是使用者作出的真实确认；不要直接照抄署名。`reference` 只在本地复制、提取和规范化，尚不上传；`register --run` 才上传与注册：

```powershell
# 选择已有本地授权音频；源文件只读，规范化副本存 workspace
.\.venv\Scripts\python.exe -m creator_dna tts reference --creator demo --config .\examples\tts\qwen.json --source D:\Authorized\reference.wav --authorize --cloud-processing --confirmation "本人姓名；声音授权依据；确认百炼北京云端处理"

# 或从 V3.0-01 库选择候选：替换 LibraryPath / CandidateID
.\.venv\Scripts\python.exe -m creator_dna tts reference --creator demo --config .\examples\tts\qwen.json --library <LibraryPath> --candidate <CandidateID> --authorize --cloud-processing --confirmation "本人姓名；片段声音授权依据；云端处理确认"

# 将 reference 返回的 ref-<hash> 填入；不带 --run 仅创建 pending 任务，无网络
.\.venv\Scripts\python.exe -m creator_dna tts register --creator demo --config .\examples\tts\qwen.json --reference <ReferenceID> --name DemoVoice --run
.\.venv\Scripts\python.exe -m creator_dna tts voices --creator demo

# 使用 Creator Skill 生成的新口播纯正文；替换 VoiceAssetID / NewScriptPath
.\.venv\Scripts\python.exe -m creator_dna tts synthesize --creator demo --config .\examples\tts\qwen.json --voice <VoiceAssetID> --script-file <NewScriptPath> --run
```

MiniMax 使用同样流程，将 `--config` 换成 `minimax.json`，并为该服务商重新登记授权参考。注册 `--name` 是 8–256 字符的 MiniMax Voice ID，例如 `DemoVoice001`；千问 name 是最多 16 字符前缀。不能把百炼 Voice ID 填给 MiniMax；更换 Provider 需要其独立音色资产。相同参考内容、端点和模型会复用已注册资产；注册失败或结果未知不会保存可用假 ID。千问降级复刻标记 `review_required`，禁止直接合成。MiniMax 首次合成前为 `pending_activation`，七天内需使用一次；未激活音色不出现在云列表，成功合成后记录使用时间。

资产位于 `workspace/creators/<creator>/voice-assets/`，包含来源/哈希、授权、创建时间、状态、模型、服务商和 Voice ID。已有云音色可复用，但须通过当前账户音色列表验证，不直接相信用户输入的 ID：

```powershell
.\.venv\Scripts\python.exe -m creator_dna tts remote-voices --config .\examples\tts\qwen.json --page 0
.\.venv\Scripts\python.exe -m creator_dna tts import-voice --creator demo --config .\examples\tts\qwen.json --voice-id <ActualCloudVoiceID> --authorize --cloud-processing --confirmation "本人姓名；已授权已有云音色和文案处理"
# 云端删除需要明确标志；本地保留 deleted 审计记录
.\.venv\Scripts\python.exe -m creator_dna tts delete-voice --creator demo --config .\examples\tts\qwen.json --voice <VoiceAssetID> --confirm-delete
```

合成通过统一 Task Manager：显示服务商、模型、真实完成段数与耗时，复用 `tasks status/logs/cancel/run --resume`。结果路径由 `output_files` 返回，包含 `speech.wav/mp3/flac`、`speech.json`、每段 WAV 和 `timeline.wav`。JSON 保存每段原始文本及字符偏移、实际音频时长、段落静音、总时间线边界、服务商请求 ID 与原始字幕。没有服务商时间戳时为 null，不编造词/句对齐；字幕相对于各段音频，以毫秒保留。后续字幕同步应以无编码延迟的 `timeline.wav` 为时间基准。

单任务串行请求，默认间隔 1.1 秒、每段最多两次，配置最多三次且恢复不清空预算。仅明确限流拒绝会自动有限重试；没有鉴权不调用 API。下载或转码失败在恢复时复用云端 Receipt；已完成段落校验哈希后复用。超时、取消或断线发生在 POST 期间会产生 `BillableOutcomeUnknown`：请先核对服务商账单/控制台，再决定允许**该一段**再次计费：

```powershell
.\.venv\Scripts\python.exe -m creator_dna tts resolve --task <TaskID> --segment 0 --accept-additional-billing --confirmation "本人姓名；核对结果；接受该段可能再次计费"
.\.venv\Scripts\python.exe -m creator_dna tasks run --task <TaskID> --resume
```

resolve 不清零预算，也不重置其他段；Receipt 下载链接过期后也可在明确确认下使用该命令。注册请求未知时禁止自动重建，请先核对真实音色后 `import-voice`；不确定 ID 时人工处理服务商控制台。进程强杀留下的任务/创作者锁按 V3.0-02 的说明确认进程已停止再处理。并行启动多个独立任务或其他客户端可能共用账户额度；每个任务的节流不能代表账户全局配额。

测试严格区分：默认 `python -m unittest discover -s tests -v` 只运行 Mock HTTP、合成测试 WAV 与本地媒体检查；这些不代表真实声音质量。真实 API 测试默认跳过，必须已有授权音色并显式启用（会计费，每段一次、不自动重试）：

```powershell
$env:CREATORDNA_TTS_REAL_TEST = "1"
$env:CREATORDNA_TTS_CONFIG = (Resolve-Path .\examples\tts\qwen.json).Path
$env:CREATORDNA_TTS_WORKSPACE = (Resolve-Path .\workspace).Path
$env:CREATORDNA_TTS_CREATOR = "demo"
$env:CREATORDNA_TTS_VOICE = "<VoiceAssetID>"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tts_integration.py -v
Remove-Item Env:\CREATORDNA_TTS_REAL_TEST
```

MiniMax 单独换配置与对应音色再运行。若要连同真实注册一起验证，额外设置 `CREATORDNA_TTS_REAL_CLONE=1`、`CREATORDNA_TTS_REFERENCE=<已授权ReferenceID>` 和 `CREATORDNA_TTS_VOICE_NAME=<合规新名称>`；测试会先注册/复用该参考音色，再合成，结束后移除 REAL_CLONE 标志。未提供真实 API Key 时，只完成代码/Mock 验证，真实服务商请求、音色相似度、可听口播质量和计费行为尚未验证。没有授权与云端处理确认时，不运行 reference 上传、register 或真实合成。

## V3.0-02：任务执行与进度反馈

安装后可直接运行本地演示，不需要模型或云端额度；原有命令照常使用。任务运行期间 stderr 展示进度，stdout 返回结构化 JSON。当前终端较窄时动态行会截短，完整状态和错误在 JSON/日志中。

```powershell
# 实际写入分块并核验哈希；百分比来自完成分块数，delay 只用于演示节奏
.\.venv\Scripts\python.exe -m creator_dna tasks create --kind local-demo --chunks 12 --delay 0.15 --run

# 实际 FFmpeg 合成测试图渲染 + FFprobe/完整解码检查，默认 3 秒
.\.venv\Scripts\python.exe -m creator_dna tasks create --kind ffmpeg-demo --duration 3 --run
```

每次产生 `task-<UUID>`，并显示状态文件绝对路径。开始/结束时间、任务/阶段耗时、当前阶段/对象、计数、百分比依据、最近消息、错误、尝试次数、产物路径均可查询。所有时间存为带时区 UTC ISO 8601。FFmpeg 原生输出秒数 / 目标时长为渲染百分比；未知时长或无进度 API 时显示未知百分比、状态、耗时或真实完成分块数。素材分析仅显示子操作计数，不将采样完成率当成整个分析完成率。

运行中整体百分比使用阶段等权重，始终明确标为“估算”，不是剩余时间预测；当前工作量未知时整体百分比为 null。真正全部阶段完成后整体为 100%。不按预设等待时间伪造进度。演示文件均标注 synthetic_only，不代表创作者质量、真实人像或云模型效果。

可先创建 pending 任务，再在前台运行；在另一终端查看或取消（以下 `<Task ID>` 替换为返回的 ID，各终端用相同 workspace）：

```powershell
.\.venv\Scripts\python.exe -m creator_dna tasks create --kind ffmpeg-demo --workspace .\workspace
.\.venv\Scripts\python.exe -m creator_dna tasks run --task <Task ID> --workspace .\workspace
.\.venv\Scripts\python.exe -m creator_dna tasks status --task <Task ID> --workspace .\workspace --watch
.\.venv\Scripts\python.exe -m creator_dna tasks history --workspace .\workspace --limit 20
.\.venv\Scripts\python.exe -m creator_dna tasks logs --task <Task ID> --workspace .\workspace --tail 100
.\.venv\Scripts\python.exe -m creator_dna tasks cancel --task <Task ID> --workspace .\workspace
```

`run` 的 Ctrl+C 取消执行；`status --watch` 的 Ctrl+C 仅停止观察。`--interval` 默认 0.5 秒，可设为 0.05–60。cancel 表示已持久化取消请求，运行者在下一次轮询停止进程树，以最终 cancelled 状态为准。任务查看/取消命令退出码 0 只表示查询/请求成功；执行命令 completed=0、failed=1、cancelled=130。history 支持 `--creator` 和 `--status` 筛选，logs 返回最近 N 条完整结构化日志。

实际素材分析也可交给引擎（需要原 V3.0-01 视频依赖）：

```powershell
.\.venv\Scripts\python.exe -m creator_dna tasks create --kind analyze-video --input "D:\Videos\CreatorA" --creator creator-a --max-frames 120 --run
```

只读扫描并保存源 SHA-256 快照；原文件变化时要求新任务。分析资产与报告留在任务尝试目录，不替换原有 video-assets/latest.json。批次部分失败时任务执行可完成，但阶段 result.analysis_status=partial，必须读取失败计数/报告；全失败任务为 failed。`--recursive`、`--sample-interval`、`--max-frames`、`--no-face-detection`、`--ffmpeg`、`--ffprobe` 可用。

`tasks create --timeout 600 --max-attempts 2 --retry-delay 0.2` 配置每阶段尝试超时、最大尝试数（1–10）与重试间隔。默认不重试；仅执行器声明的可重试错误且阶段安全重复执行时自动重试。所有尝试独立保存，失败文件不进入已完成输出清单，不覆盖已完成结果。

```powershell
# 显式恢复失败/取消任务：校验并跳过成功阶段，未完成安全阶段从新尝试目录重跑
.\.venv\Scripts\python.exe -m creator_dna tasks run --task <Task ID> --workspace .\workspace --resume

# 六种实际本地工程验收：完成、FFmpeg、重试、失败、超时、跨 CLI 进程取消
.\.venv\Scripts\python.exe scripts/validate_v3_tasks.py --workspace .\workspace\v3-02-validation
```

恢复是人工发起的新一次有限执行，尝试编号累加；不是远端模型计算或媒体帧的真正断点续算。完成文件缺失/改变或阶段未声明安全重复执行会拒绝复用。强制关机/终止可能留下 .lock 和 running 状态；先确认状态记录的父/子进程及其子树全部停止，再移除该任务的 .lock 后 `--resume`，不自动偷锁。Windows 建议使用较短 workspace 路径，尤其是大量素材证据的嵌套输出。

数据在 `workspace/tasks/<Task ID>/`：task.json 保存计划/参数，status.json 原子保存状态（Schema 3.0-02），logs.jsonl 统一保存日志；stages/<阶段>/attempt-NNN/ 保存请求、原始事件、stdout/stderr、结果和 outputs。阶段检查点携带输出 SHA-256，为后续恢复保留 job ID/历史定位接口。

TTS/口型同步/字幕模块按 StageExecutor 接口注册，使用 StageContext 报告真实工作量并把文件写入独立 output_dir；渲染模块可复用原生 FFmpeg 适配器。完整接口、重试/取消限制及接入示例见 [V3.0-02 设计](docs/V3-02-DESIGN.md)，工程证据见 [V3.0-02 验证](docs/V3-02-VALIDATION.md)。

## V3.0-01：源视频分析与素材资产库

在项目虚拟环境安装可选视频依赖，准备 PATH 中的 FFmpeg / FFprobe（建议 6.1+）：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[video]"
.\.venv\Scripts\creator-dna.exe doctor
.\.venv\Scripts\creator-dna.exe analyze-video --input "D:\Videos\CreatorA" --creator creator-a --workspace .\workspace
```

每次只指定同一创作者的一个素材文件夹；默认扫描该层，加 `--recursive` 扫描子目录。仅分析 mp4/mov/mkv/webm/avi/m4v；忽略音频和转写稿，同名不同格式视频各自记录。与原有 `prepare` / `distill` 分开执行，不需要 ASR、已激活 Skill 或 Codex 登录，不调用任何外部 AI 服务，也不上传人像或声音。

分析自动记录编码尺寸、考虑 SAR/旋转后的显示尺寸和比例、精确有理数帧率、时长、编码、音轨/采样率、旋转和像素宽高比。选用第一个非封面视频流，记录全部音频流。平均/标称帧率不一致仅提示可能存在 VFR，不宣称完成逐帧 VFR 检验。SAR 缺失时按 1:1 计算并明确标记假设。原视频只读且保存 SHA-256 引用，不复制大视频。

FFmpeg 全片解码检测像素变化，生成连续镜头候选及秒级时间戳；长镜头再分成最长 12 秒的索引片段，不输出裁剪视频。关键帧时间记录实际解码 PTS（以源视频起点为零），正确处理旋转和非正方形像素。联系图每页最多 24 帧，每视频默认最多采样 120 帧；达到上限会降采样并明确标记，未覆盖候选继续保持未知。

本地 OpenCV 正面人脸检测记录采样检出比例、人脸框中心/面积及面部区域清晰度代理；没有 OpenCV 时仍可分析，但人脸数据为 `null`。人脸检出比例不是人物出镜时长比例，检测器不识别创作者身份；两张采样帧像素差也不是头部运动测量。相似灰度缩略图仅用于筛查重复画面，固定场景数、动作重复、嘴部可见性、表情、身体构图、字幕视觉样式和 B-roll 语义仍待人工确认。

候选建议类别：A=口型同步人像，B=无声动作/过渡，C=B-roll，D=背景/场景，E=不适合复用。所有候选 `status=pending_review`、`category=null`、`confidence=null`；`suggested_categories` 只是可追溯启发式建议，不是模型分类结论。没有检出人脸不等于没有人物。异常视频单独记录错误，继续分析其余文件。

每次运行返回绝对路径，产物保存在：

```text
workspace/creators/<creator>/
  video-assets/latest.json                  最近一个有成功素材的资产库引用
  runs/video-analysis-<UUID>/
    run.json                                completed / partial / failed / interrupted
    analysis-config.json                    检测阈值与采样配置
    partial-library.json                    按文件追加的处理中检查点（未完成索引，不能用于生产）
    library.json                            Schema 校验后的完整资产清单
    report.md                               中文报告、时间戳、建议和待审核问题
    review-template.json                    人工审核工作表
    evidence/<source-id>/
      probe.json                            FFprobe 原始响应
      scene-detection.log                   镜头检测原始日志
      frame-0000.png                        归一化显示比例后的关键帧
      contact-000.jpg                       带时间标签的联系图（可分页）
```

`library.json` 合同见 `src/creator_dna/schemas/video-library.json`，Schema 版本为 `3.0-01`。`sources[]` 嵌套 `metadata` / `shots` / `frames` / `candidates`，候选通过 `shot_id` 和 `evidence_frame_ids` 引用证据。相似画面组和按实际显示尺寸/比例/帧率分组的 `output_templates` 位于根节点；按总时长标记首选模板，不强制混用不一致的素材。资产库引用最新可用运行，重跑保留历史；全部文件失败时保留报告，不替换 latest。退出码：completed/partial 为 0；全部失败、依赖缺失或参数错误为 1；人工中断为 130。自动化调用应同时检查 `status` 和失败计数。

下游模块必须使用 `library.json`，仅将候选作为筛选入口，并独立核验源文件 SHA-256、原片上下文和人工审核结果。`review-template.json` 可按候选 ID 填写 A–E、场景 ID、审核者和证据；目前只是工作表，不会自动合并或激活素材，自动测量保持原始状态。

可调参数示例：

```powershell
.\.venv\Scripts\creator-dna.exe analyze-video --input .\materials\creator-001 --creator creator-001 `
  --scene-threshold 0.3 --sample-interval 1 --max-frames 240 --segment-seconds 10 --timeout 600
```

`--scene-threshold` 越低越敏感，范围 0–1（不含端点）；`--sample-interval` / `--segment-seconds` 为正秒数，`--max-frames` 为每视频 1–2000；`--timeout` 是每次工具调用的秒数，不是整个批次时限。`--no-face-detection` 禁用人脸检测；`--ffmpeg` / `--ffprobe` 可指定本地工具完整路径。联系图使用 Pillow；OpenCV 自带本地 Haar 权重，无须下载人像模型。

复现合成工程验证（拒绝覆盖已有目标）：

```powershell
.\.venv\Scripts\python.exe scripts/validate_v3_video.py --input .\materials\v3-synthetic --workspace .\workspace\v3-validation
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

验证包含横屏切镜、竖屏不同帧率、旋转/SAR 和异常文件；只验证文件、时间戳和数据合同，不能作为真实创作者视觉质量或口型同步效果的证据。下一步优先人工选择短、正面、清晰、无遮挡的人像片段，在授权本地环境验证口部重定向的身份保持和时间一致性，再独立推进声音、字幕及剪辑模块。详见 `docs/V3-01-DESIGN.md`。

## 安装

需要 Python 3.10+。在本项目目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\creator-dna.exe doctor
```

蒸馏和生成需要安装、登录 Codex CLI，使用 CLI 已有的认证及默认模型，无须另建 API Key 系统。`--model` 可显式指定模型。Codex 通过官方非交互 `exec` 的 stdin、`--output-schema` 和 `--output-last-message` 接口接入，不经过 shell 拼接提示词。参见[官方非交互执行文档](https://developers.openai.com/codex/noninteractive)。

从视频或音频做 ASR 时，还需要 FFmpeg 以及本地转写依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[asr]"
```

默认采用 faster-whisper `small`、中文、CPU/int8；首次使用模型名时可能下载权重。离线使用 `--asr-model "D:\Models\faster-whisper-small"` 指向模型目录。支持 `--language auto`、`--device cuda`、`--compute-type float16`；GPU 相关依赖按本机环境配置。`doctor` 只检查命令或包能否找到，不会下载模型，也不代表已登录或模型可运行。

## 四步流程

### 1. 输入素材文件夹

素材目录按创作者组织，风格参考与人像原片分开放置：

```text
materials/
  creator-001/             第一位创作者的风格参考视频、转写稿
    raw_portraits/         用户手动加入的无字幕、未剪辑动态人像原片
  creator-002/             第二位创作者的风格参考视频、转写稿
    raw_portraits/         第二位创作者的人像原片
```

可继续新增其他子文件夹。每个子文件夹只放同一创作者的参考素材，运行时选择对应子文件夹，不把多个创作者的父文件夹一起蒸馏。例如放好素材后运行：

```powershell
.\.venv\Scripts\creator-dna.exe distill --input .\materials\creator-001 --creator creator-001
```

建议放入同一创作者的 10–20 条风格稳定的口播视频。每次输入一个文件夹；默认扫描该层，`--recursive` 包含子目录。

- 视频：mp4、mov、mkv、webm、avi、m4v。
- 音频：mp3、wav、m4a、flac、aac、ogg。
- 转写：UTF-8 txt、md、srt，或带 `segments` 的 JSON。
- 同名转写稿优先于 ASR，例如 `01.mp4` + `01.txt` 算一个样本。每组只允许一份媒体和一份转写稿，避免重复计数或歧义。
- 纯文本按非空行划分 segments，时间戳记为 null；SRT 和 JSON 保留已有时间戳。
- 原始文件始终只读；素材目录和 workspace 必须互不包含。

JSON 转写稿示例（start/end 均为秒，或同时为 null）：

```json
{
  "language": "zh",
  "segments": [
    {"start": 0.0, "end": 3.2, "text": "你有没有遇到过这样的情况？"},
    {"start": 3.2, "end": 7.0, "text": "先看一个例子，再说结论。"}
  ]
}
```

可单独完成预处理，检查转写质量，不调用 Codex：

```powershell
.\.venv\Scripts\creator-dna.exe prepare --input "D:\Videos\CreatorA" --creator creator-a
```

### 2. 分析与蒸馏

```powershell
.\.venv\Scripts\creator-dna.exe distill --input "D:\Videos\CreatorA" --creator creator-a
```

系统扫描文件夹，提取音频并转写，再把标准化数据交给元 Skill；完成风格结论和 Creator-Skill 的生成、校验及保存。每个风格规则必须引用实际 segment 的原文证据。每次成功蒸馏新增 v001、v002 等版本，同时保留 `style-profile.json`、`SKILL.md` 和包内 `references/style-guide.md`。

如果已有转写稿，可加 `--asr none`；遇到无转写的媒体会明确报错。重复运行按媒体哈希、转写稿哈希和 ASR 配置复用缓存。失败任务保留已完成转写、请求、原始 AI 结果和日志；纠正错误后使用相同命令重跑即可，不会把无效结果发布为版本。输入超出 `--max-input-chars`（默认 240000 字符）时明确停止，不静默截断素材。

新 Skill 为草稿。人工检查内容和局限后确认；这也是对生成规则的一次审核：

```powershell
.\.venv\Scripts\creator-dna.exe skills list --creator creator-a
.\.venv\Scripts\creator-dna.exe skills approve --creator creator-a --version v001
```

确认后成为默认激活版本。直接修改已保存文件会导致完整性校验失败。如需修订，先复制 SKILL.md 到工作文件，编辑后创建新草稿，再确认：

```powershell
.\.venv\Scripts\creator-dna.exe skills revise --creator creator-a --version v001 --skill-file ".\edited-skill.md"
.\.venv\Scripts\creator-dna.exe skills approve --creator creator-a --version v002
```

### 3–4. 新选题与文案生成

```powershell
.\.venv\Scripts\creator-dna.exe generate --creator creator-a --topic "AI手机会如何改变我们的生活"
```

可指定 `--skill-version v001`，也可用 `--brief-file .\brief.txt` 提供受众、目标时长、已核实的事实或限制。系统只读取已确认的 Skill 包，不再读取原视频、转写或元 Skill。输出可朗读的 Markdown 文案、创作备注、使用版本和运行记录。

所有相关命令都可用 `--workspace "D:\CreatorDNA-data"` 指定持久化目录；同一个创作者的后续操作使用相同 workspace 与 creator ID。省略 creator ID 时按输入路径生成，CLI 结果中会给出它。

## Skill 独立复用

```powershell
.\.venv\Scripts\creator-dna.exe skills export --creator creator-a --version v001 --destination ".\.agents\skills\creator-creator-a"
```

导出目录必须不存在，不覆盖已有内容。导出包仅含 SKILL.md 和包内 references，可直接在其他项目的 `.agents/skills/` 中调用，不需要安装 CreatorDNA 或保留源素材。参见[官方 Skill 发现机制](https://developers.openai.com/codex/skills)。

## 架构与数据

采用单仓库、模块化单体；确定性文件、媒体、存储与任务模块和 AI 分析、评估模块分开，ASR、Agent、TTS 和口型 Provider 保留显式接口。

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| CLI | src/creator_dna/cli.py | 用户命令、配置和错误反馈 |
| Input Manager | input.py | 文件夹扫描、同名匹配、哈希和素材身份 |
| Media Processor | media.py | 转写导入、FFmpeg、可替换 Transcriber |
| Agent Adapter | agents.py | 可替换 AgentAdapter；当前为 Codex CLI |
| Workflow | workflow.py | 蒸馏与生成编排、请求快照和失败记录 |
| Skill Manager | skills.py | 校验、版本、确认、修订、导出 |
| Workspace Manager | storage.py | 原子写入、任务记录和每创作者互斥锁 |
| Contracts | contracts.py、schemas/ | 统一可扩展 JSON 数据协议 |
| Evaluation | evaluation.py | 引文校验、配置阈值、独立评分组和报告 |
| Quality Workflow | quality.py | 有限修订、人工反馈、固定集批量与版本对照 |
| Task Manager | tasks.py、task_worker.py、task_progress.py | 分阶段执行、进度、取消、超时和恢复 |
| TTS / Voice Assets | tts_workflow.py、tts_providers.py、voice_assets.py | 授权音色、创作者默认绑定、真实音频及收费请求记录 |
| Video Assets | video_assets.py、video_style.py、portrait_sources.py | 视频测量、风格 profile 和人像原片来源边界 |
| Lip Sync / Portrait | lip_sync_workflow.py、musetalk_runner.py、portrait.py | 独立口型模型、整片源锁与使用账本 |
| Production / Render | production.py、subtitles.py、timeline.py、render.py、video_workflow.py | 字幕、场景编排、统一视频任务和成品检查 |

```text
workspace/creators/creator-a/
  manifest.json
  transcripts/<source-id>/<cache-key>.json
  skills/
    active.json
    v001/
      SKILL.md
      style-profile.json
      references/style-guide.md
      metadata.json
  outputs/generate-<run-id>.md
  runs/<task>-<run-id>/
    run.json
    manifest.json / request.json
    transcripts.json / generation-result.json
    agent/prompt.txt, schema.json, response.json, stdout.log, stderr.log
```

`style-profile.json` 是未来其他模块复用的数据，不需反向解析 Skill 文本；每条规则携带维度、指令、置信度和证据。时间记录使用带时区的 UTC ISO 8601。素材与转写内容可能在本地日志中保存，workspace 默认不提交到版本控制。

元 Skill 只有一份权威来源，位于 `.agents/skills/creator-dna-distiller/`；打包时直接作为 Python 资源包含，所以安装后的 CLI 在其他目录也能找到它。

## 验证与当前限制

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

2026-10-12 使用项目 `.venv` 执行上述命令：**187 项测试，184 项通过、3 项真实服务集成测试跳过，0 失败**。默认回归不启用真实 TTS、口型或整片服务测试；本文案例来自已保存的独立真实任务。

测试覆盖 V1.0 回归、V2.0 的评估合同与版本闭环，以及 V3 的任务、音色绑定、字幕、口型接口、原片来源、单源锁定、上半身构图、编排、渲染和恢复。AI 合同测试使用明确标记的假适配器，不消耗真实 AI 调用，不能证明真实风格质量。工程检查保证证据引用存在，不保证模型解释正确；真实质量仍需人工盲评。

`examples/materials/` 是为演示命令制作的合成转写样本，不是真实创作者素材。它们可用于从文件夹到文案的模型接入测试：

```powershell
.\.venv\Scripts\creator-dna.exe distill --input .\examples\materials --creator demo --asr none
.\.venv\Scripts\creator-dna.exe skills approve --creator demo --version v001
.\.venv\Scripts\creator-dna.exe generate --creator demo --topic "为什么整理工作文件比购买新工具更有用"
```

如果强制结束进程，可能留下 `.lock` 文件。确认该创作者已没有任务运行后再删除该锁；一般失败会自动释放锁。

## V2.0 单篇质量闭环

以下命令使用与蒸馏相同的 workspace/creator。旧 `generate` 仍只生成；新 `quality` 负责生成、评估和有限修订。

```powershell
.\.venv\Scripts\creator-dna.exe quality --creator demo --topic "为什么睡前准备明天要用的东西能减少忙乱" --max-revisions 2
```

最多初评一次、修订两次，每次修订重新评估。`--max-revisions 0` 只生成并评估。结果 JSON 给出 run_id、status、output 和 history；输出文件是纯口播正文，便于继续评估。Ctrl+C 停止本次任务及 Codex 进程树，保留已完成的稿件、报告和历史。未达标、无法评估和修订失败均有原因及人工处理建议，不自动修改 Creator Skill。

只评估一篇已有稿件（UTF-8 纯正文；不要把版本备注或标题当作口播正文）：

```powershell
.\.venv\Scripts\creator-dna.exe evaluate --creator demo --script-file .\draft.md --topic "文件整理"
.\.venv\Scripts\creator-dna.exe quality --creator demo --script-file .\draft.md --topic "文件整理" --max-revisions 1
.\.venv\Scripts\creator-dna.exe evaluation-config --destination .\evaluation-config.json
```

编辑导出的配置，通过 `--config .\evaluation-config.json` 使用自定义维度和最低分。维度具有唯一 id、group（style/quality）、description、minimum（0–5）。必须分别保留风格与内容质量组，原创性允许值单独由 max_originality_risk 配置。默认评估语言、结构、文本节奏、情绪、辨识度及内容质量，各项至少 3/5，允许 low 原创性风险；这些是运行阈值，**不是已经达到的测试成绩**。

评估生成 `evaluation-report.json` 和同名 Markdown：保留配置、Skill 哈希、正文哈希、各维度理由/引文、问题/建议和局限。引文必须匹配实际正文、规则 instruction 或 profile 中已经核验的 evidence；不存在的规则、错引文、缺字段均拒绝采用。

- `passed`：所有配置维度有有效评分且达标，原创性风险可判断并符合配置。
- `failed`：可评估但有维度或原创性风险不达标。
- `unable`：缺少 Skill/资料、评分为 null、原创性 unknown、Agent 失败或结果无效。停止自动修订，保留错误。

JSON 的 status 表示质量状态。退出码 0 表示命令写出结果，不代表评估通过；调用方应读取 status。非法参数/配置返回 1，中断返回 130。批量结果中检查 unable 和计划/完成次数。

AI 分数是主观分级，没有综合“真实相似度”。评估只读取独立 Skill、风格 profile 的已核验证据和当前稿件，不需要原视频。没有完整原稿时，原创性只覆盖给出的证据，low 不能证明全文原创；事实没有经过联网核验。文本不能证明语速、声音或真实停顿。Creator Skill 自身仍可独立导出复用。

Evaluation/Optimizer Skill 也可将其 `.agents/skills/` 下的整个目录复制到其他项目独立调用，包内 references 携带输出 Schema；无需安装本 Python 包。CLI 请求已经附带 Schema 时，不需要再读取这些文件。

## 人工反馈与候选优化

阅读稿件和报告，按 `examples/v2/feedback.json` 填写真实署名与意见。示例是合成格式模板，不是有效的人工质量证明。

```powershell
.\.venv\Scripts\creator-dna.exe feedback --creator demo --run-id <quality或evaluate运行ID> --file .\human-feedback.json
.\.venv\Scripts\creator-dna.exe quality --creator demo --topic "原选题" --script-file <对应运行的script.md> --feedback-id <feedback运行ID> --max-revisions 2
```

人工可用 status_override 覆盖状态，或用 dimension_overrides（id/score/reason）覆盖维度；留 null/空数组表示补充意见。反馈追加保存，原 AI 报告不变，独立 human-view.json 保留人工视图。覆盖只适用于反馈所指的原稿、版本和文件哈希；修订稿仍重新接受 AI 评估，原稿的人工通过意见不会自动传递给修订稿。

Skill 级优化需至少两次不同的 development 运行出现重复风格偏差，并有对应人工反馈：

```powershell
.\.venv\Scripts\creator-dna.exe skills optimize --creator demo --version v001 --batch <development批量运行ID> --feedback <feedback运行ID>
```

`--batch`、`--feedback` 可重复。系统不接受 acceptance 数据或无关联的反馈，不因单篇失败创建候选。优化仅修订有证据规则的实施方式，保留原 profile，并对归档真实转写快照重核证据；如果原蒸馏运行已被删除，需恢复该快照后才能生成候选。候选为新草稿，记录父版本、训练选题、反馈、诊断和变更说明，不修改激活版本。

## 固定测试集、版本对照与发布

测试集 JSON 遵循 `src/creator_dna/schemas/dataset.json`。cases 包含唯一 id、topic、category（regular/cross_topic/distant/complex）、brief 和可选 human_reference（无人工样本时为 null）。human_reference 仅用于后续人工对照，绝不进入生成或 AI 评分。purpose 将开发集 development 与最终验收集 acceptance 区分。

```powershell
.\.venv\Scripts\creator-dna.exe batch --creator demo --skill-version v001 --dataset .\examples\v2\development.json --repeats 2
.\.venv\Scripts\creator-dna.exe batch --creator demo --skill-version v001 --dataset .\examples\v2\acceptance.json --repeats 2 --model <本机可用模型ID>
.\.venv\Scripts\creator-dna.exe batch --creator demo --skill-version v002 --dataset .\examples\v2\acceptance.json --repeats 2 --model <相同模型ID>
.\.venv\Scripts\creator-dna.exe compare --creator demo --baseline <v001验收batch运行ID> --candidate <v002验收batch运行ID>
```

batch 可测试草稿；默认不修订，用于观察原始稳定性。`--max-revisions N` 可测试相同修订策略，两边必须一致。单个子任务失败记为 unable，其他选题继续；报告包含计划次数、完成次数、每次运行、各维度均值、类别统计和错误。对照必须使用完全相同的固定测试集、重复次数、评分配置、Agent 配置与修订上限；程序核对结果与实际子任务、稿件及 Skill 哈希。

```powershell
.\.venv\Scripts\creator-dna.exe skills approve --creator demo --version v002 --comparison <compare运行ID> --confirmation "审核人姓名：已审阅对照和稿件，同意发布"
.\.venv\Scripts\creator-dna.exe skills list --creator demo
.\.venv\Scripts\creator-dna.exe skills diff --creator demo --left v001 --right v002
.\.venv\Scripts\creator-dna.exe skills rollback --creator demo --version v001
.\.venv\Scripts\creator-dna.exe creators
```

V2 自动优化候选及其修订后代只能在以下条件下激活：与已确认的比较基线做 acceptance 对照（通常是父版本，草稿续修沿用原基线）；候选每次通过；基线没有 unable；风格和内容质量各维度均值不下降；验收选题与历代优化选题不重叠；显式提供人工确认。Codex 发布对照必须用 `--model` 固定模型。选题重叠检测规范化空白、大小写和全半角，**不能检测语义近似选题**，仍需人工划分数据集。门禁是工程条件，不宣称真实质量提升。

为兼容 V1.0，原蒸馏草稿和普通人工 `skills revise` 草稿仍支持原 `approve` 确认方式；这条人工审核路径不宣称通过 V2 验收。自动优化候选不能通过普通 revise 绕过其门禁。回滚只切换至已确认且完整的历史版本，不删除后续版本，保留回滚记录。每个创作者的数据、版本和结果独立存储。

## 新增数据目录

```text
workspace/creators/<creator>/
  skills/vNNN/metadata.json          来源、变更说明、父版本、测试/反馈/发布关联
  runs/evaluate-<uuid>/              script.md、评估 JSON/Markdown、请求/原始结果
  runs/quality-<uuid>/
    request.json、script.md、evaluation-report.json/.md
    revision-history.json/.md
    attempt-000/、attempt-001/        每次正文、生成结果、评估和 Agent 日志
  runs/feedback-<uuid>/              feedback.json、human-view.json
  runs/batch-<uuid>/                 固定集/配置快照、batch-report.json/.md
  runs/compare-<uuid>/               comparison.json/.md
  runs/optimize-<uuid>/              诊断、模型原始结果、candidate-SKILL.md
  runs/rollback-<uuid>/              版本切换记录
```

JSON Schema 权威定义位于 `scripts/build_schemas.py`；修改定义后运行该脚本重新生成资源。所有模型调用记录完整输入、输出 Schema、原始 JSON 与配置；坏结果不会成为正式评分或候选。V1.0 元数据保持可读，无需覆盖迁移。

## 可执行完整例子与真实验证

已有已确认 demo Skill 时，从新选题到评估/有限修订的完整流程：

```powershell
.\examples\v2\run-cycle.ps1 -Creator demo -Topic "为什么先整理桌面再购买收纳工具" -MaxRevisions 2
```

如尚无 demo，先执行上面的 `examples/materials` 蒸馏及人工确认命令。所有 examples 选题和素材均为合成演示；使用真实创作者时替换 creator 和输入素材。

可选真实 Agent 集成测试，独立于 unittest，使用已安装并登录的 Codex、有效套餐额度及已确认 Skill：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/validate_v2_agent.py --source-workspace workspace --creator demo --batch
```

该脚本只复制 Skill 包到新建的隔离目录，不改原包、素材或激活版本；最多 3 次真实调用验证初评/修订/复评，`--batch` 再增加 4 次生成/评估。默认每次超时 300 秒。调用次数与实际状态写入 integration-summary.json，不强制分数达标，也不伪造人工反馈或发布候选。

真实创作者产品验收需另行收集素材、固定最终验收集并做人工盲评：隐藏版本和生成方式，评分 1–5，记录能否直接使用、修改分钟数及复制/漂移问题，再解盲比较 V1.0/V2.0 和不同版本。4/5、80% 可用率只是探索性目标，当前没有真实数据支持。工程与接入验证见 `docs/V2-VALIDATION.md`；设计与兼容取舍见 `docs/V2-DESIGN.md`。
