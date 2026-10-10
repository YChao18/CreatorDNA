# CreatorDNA 配置与验收检查清单

检查日期：2026-10-10（Asia/Shanghai），项目包 0.3.0，实际工作树。与 [V3_AUDIT_REPORT.md](V3_AUDIT_REPORT.md) 配套。状态只描述本机本轮事实；“静态通过”不等于“真实接口通过”。

## 1. 配置/验证总表

| 模块 | 配置项 | 用途 | 当前状态 | 需要的操作 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| 主运行环境 | `.venv/Scripts/python.exe` | CLI/worker/测试解释器 | 已安装并验证，Python 3.14.6，editable 0.3.0 | 明确使用此解释器或激活 .venv；默认 python 缺包，不能替代 | P0 |
| 基础依赖 | jsonschema、PyYAML | 输出/配置合同 | 已安装并导入，31 Schema 有效 | 后续环境变化后重跑 unittest/pip check | P1 |
| 依赖一致性 | huggingface-hub 1.22.0 / click 8.4.1 | ASR 下载/周边 CLI 依赖 | 版本冲突，pip check 退出 1 | 后续选择兼容版本组合修正，避免继承系统包导致再次污染；本轮未升级 | P1 |
| workspace | 各命令 `--workspace` | 所有运行、草稿、媒体副本、失败产物 | 根 workspace 可读写；不同 V3 子 workspace 不共享 Voice/Skill | 固定生产 workspace，用绝对路径；与源目录互不包含 | P0 |
| FFmpeg/FFprobe | `--ffmpeg/--ffprobe`；TTS/口型/render 同名字段 | 媒体提取、测量、编码、字幕和验证 | 6.1.1，本地执行通过 | PATH 或绝对路径；明确不同配置都指向可用工具 | P0 |
| 图片/视频分析 | Pillow、opencv-python-headless | 采样/联系图/本地人脸代理/图片素材 | 已安装并执行；非真实人像质量证明 | 保留 `[video]` 能力；字体/检测结果另做人工 QA | P1 |
| ASR | `--asr faster-whisper/none`、`--asr-model` | 媒体→转写 | small 本地模型已实际通过；base/medium 仅可见缓存目录 | 优先传实际 small 本地路径避免下载；导入 sidecar 可用 none | P1 |
| ASR 参数 | `--language zh/auto`、`--device cpu`、`--compute-type int8` | 推理配置 | CPU/int8 本轮真实通过；GPU ASR 未测 | 按本机选择，不把 MuseTalk GPU 要求混入 ASR；听验准确率 | P1 |
| Agent CLI | `--codex-bin` / 工作流 `agent.binary` | 蒸馏/创作/评价/优化/导演 | Codex 0.146.0 可启动、认证状态可读；请求未测 | 使用已配置 CLI；只读检查配置和认证，随后在批准额度内少量真实请求 | P1 |
| Agent 模型 | `--model` / `agent.model` | 固定推理模型 | 代码可传，实际可用模型/配额未请求验证 | 使用账户已有模型；版本对照明确固定同一模型，勿随意填写不存在 ID | P1 |
| Agent 边界 | AgentAdapter | 换外部推理平台 | 接口存在，CLI 当前仅 CodexAdapter | 不需要分别配置“蒸馏 Key/评价 Key/导演 Key”；新增 Provider 需实现适配器，非填 Key 就能启用 | P2 |
| Creator Skill | `--creator`、`--skill-version` / `skill_version` | 新主题文案和可选 V2 | creator-001/v002、demo/v001 在根 workspace 可完整读取 | 正确选择 creator/workspace；人工批准及真实新稿验收；不用 synthetic demo 证明真实质量 | P0（新主题） |
| V2 评分 | `--config`；evaluation-config 默认导出 | 维度/阈值/原创性门禁 | 工程通过，真实盲评未完成 | 用真实固定集/人工反馈；分别保留 style/quality，读取业务 status | P1 |
| V2 对照/发布 | dataset purpose、repeats、model、comparison、confirmation | 优化及独立验收 | 工程门禁通过，真实产品对照待测 | development 与 acceptance 分离，固定参数/模型；自动候选须对照 + 人工确认 | P1 |
| Qwen 凭据 | `api_key_env=DASHSCOPE_API_KEY` | 百炼复刻/合成鉴权 | Process/User/Machine 均缺失 | 在匹配地域百炼获取 Key，设置环境变量；不要写 JSON/.env 后期待自动加载 | P0（选 Qwen） |
| Qwen 端点/模型 | qwen.json 的 `provider=qwen`、`base_url`、`model` | 协议、地域和模型匹配 | 本地校验通过；真实接口未测 | 保持 qwen3-tts-vc-2026-01-22；北京/新加坡端点须匹配 Key，音色模型一致 | P0（选 Qwen） |
| Qwen 支持参数 | speed=1.0、emotion=null、subtitles=false | 避免传不支持参数 | 示例正确；真实效果未测 | 不能用情绪/语速非默认参数假设 VC 支持；format/sample_rate 可由本地转码统一 | P1 |
| MiniMax 国内凭据 | `api_key_env=MINIMAX_CN_API_KEY` | 国内接口鉴权 | Process/User/Machine 均缺失 | 用国内平台账户获取 Key，配 minimax-cn.json | P0（选国内 MiniMax） |
| MiniMax 国际凭据 | `api_key_env=MINIMAX_API_KEY` | 国际接口鉴权 | Process/User/Machine 均缺失 | 用国际平台匹配账户，配 minimax.json；不必为不选的 Provider 同时配置 | P0（选国际 MiniMax） |
| MiniMax 端点/模型 | provider=minimax、base_url、model | clone + 同步 TTS | api.minimax.cn/io、speech-2.8-hd 示例静态通过 | 真实注册/合成、账户额度、字幕返回待测；仅使用 factory 允许模型 | P0（选 MiniMax） |
| 声音参考 | `tts reference --source` 或 `--library --candidate` | 提取24kHz mono PCM16 | 本地工程处理通过；没有正式声音参考资产 | 使用真实清晰单人声音，指定与源目录分离的 workspace；准备真实授权记录 | P0 |
| 声音授权 | `--authorize --cloud-processing --confirmation` | 音色及 Provider 云处理范围 | 无正式参考/音色 | 依据真实授权填写署名/用途；不是形式性照抄 flags；不隐式扩大授权 | P0 |
| 声音注册/导入 | `tts register --reference --name` 或 import-voice `--voice-id` | 创建/复用实际 Voice ID | root workspace 真音色 0；HTTP 替身测试通过 | 另行受控真实注册/核验导入，保存返回 `voice_asset`；未 --run 只排队 | P0 |
| 声音状态 | ready/pending_activation/review_required | 防降级音色/过期 | 真资产缺失；Qwen fallback 无同资产审核通过 CLI | MiniMax 七天内首次合成；Qwen fallback 优先改善参考重新注册，不手改状态 | P1 |
| Provider/账户隔离 | base_url + api_key_env + model | Voice scope | 已实现合同，未真实切换 | 换账户用新变量名，注册/导入对应音色；旧 voice_asset 不跨账户复用 | P1 |
| TTS 请求参数 | request_timeout、request_interval、max_attempts、retry_delay、segment_chars、paragraph_pause | 超时/节流/预算/分段 | 实现及替身测试通过 | 保留合理值；真实失败后核对 receipt/账单，优先 resume 原任务，勿新建任务盲目重发 | P1 |
| TTS 输出 | format、sample_rate、speech.json、canonical_audio | 统一音频、后续时间基准 | 本地 WAV/MP3/解码工程通过；真语音未测 | 实测时长/哈希/完整解码并听验；字幕绑定 canonical PCM，不用压缩文件粗估时间 | P0 |
| MuseTalk 仓库 | lipsync 配置 `repository` | 外部模型代码/模型相对布局 | workspace/models/MuseTalk 不存在 | 固定上游版本部署；使用绝对路径，避免启动 CWD 改变 | P0 |
| MuseTalk Python | lipsync 配置 `python` | 独立运行环境 | 示例独立 Python 不存在 | 独立兼容 Python 环境，配置实际绝对 exe 路径，不污染主 .venv | P0 |
| MuseTalk 模型 | models/musetalkV15、sd-vae、whisper、dwpose、face-parse-bisent、s3fd | 真实口型/脸部定位/音频特征 | 11 个必需文件缺失 | 按 capabilities 和官方模型目录准备完整文件；本轮未下载大型模型 | P0 |
| MuseTalk 原生依赖 | torch/cv2/numpy/diffusers/transformers/librosa/omegaconf/mmpose/mmdet/mmcv/einops/face_detection | 推理和 MMLab 扩展 | 指定环境不存在，无法确认组合可用 | 在独立环境实际导入，验证 mmcv.ops；find_spec 不等于二进制兼容 | P0 |
| MuseTalk GPU | gpu_id、batch_size、float16、parsing_mode、fps、seed | 真实本地推理 | RTX5070已枚举，runtime/VRAM峰值未测 | 支持 sm_120 的 Torch/CUDA；doctor 真实 kernel 后先 5–10 秒；当前适配器 fps=25 | P0 |
| 人像候选库 | `portrait.library` / lipsync `--library` | 分析和源证据 | 实际库18源/260候选，源哈希当前匹配 | 用实际 library 路径；自动 A 建议不等于可用人像 | P0 |
| 人像审核 | `portrait.review` / `--review` | eligible、安全范围、action/scene/pose | 189项、eligible=0 | 完整观看原片，真实填写 reviewer/evidence/安全范围和所有适合度项 | P0 |
| 人像来源/授权 | `portrait_authorization` 或 lipsync consent | 绑定源和新音频 | workflow 示例 false/零哈希 | 填真实 creator/source_sha256/confirmed_at/confirmation/local 范围；独立 consent 还绑定实际 audio_sha256 | P0 |
| 人像复用 | portrait.candidate/cooldown/max_uses；timeline/position | 避免重复动作及区间 | 确定性合同通过 | 匹配实际素材量；不足直接补料，不调高次数掩盖重复；人工检查切口 | P1 |
| 姿态切换 | b_roll_hooks / usage.transition | 遮蔽不兼容镜头边界 | 已生成标记，完整生产未强制消费 | 后续小修复传入编排/验收；当前手工把边界加入 direction/timeline | P1 |
| 静态人物图 | portrait.image / lipsync `--image` | 图片口型 | 接口存在，真实模型未测 | 只能声称静态图口型，不代表身体动作生成；不得拿此验收自然动作方案 | P1 |
| Video Style Profile | workflow.profile / production `--profile` | 画幅/帧率/视觉证据 | 现存 profile 可读；manual=0、coverage=0 | 做人工 review 后生成新 profile；字幕/布局仍要明确填生产配置 | P1 |
| 生产配置 | `production_config` / production `--config` | output、subtitle_style、layout、background_asset_id | 可导出，默认字体并非作者字体 | 用 production config 的新输出编辑真实字体/字号/颜色/边距/背景/布局，再引用路径 | P1 |
| 素材资产 | workflow.assets / `--registry` | B-roll/背景/人物媒体 | 真实库导入260项待分类；无新选题验收资产 | 写真实 kind/rights/rights_note/topics/description/源范围；unknown/denied 不能入成片 | P0（混合成片） |
| 素材导入 | production import `--asset --id --kind --media-type --source --rights --rights-note --topic` | 加本地授权素材 | 本地工程可执行 | 每次使用返回的新 assets.json；不覆盖旧清单，不需图片 API Key | P1 |
| 已有人像音频绑定 | `--portrait-audio --audio-offset`、audio_sha256 | B-roll 返回后仍正确口型 | 工程校验通过 | 必须绑定同一主 PCM 音频；原声人物不能用于新声音冒充口型 | P0（复用分支） |
| MiniMax 字幕 | TTS `subtitles=true` | 下载实际句级时间戳 | 示例开启，真实响应未测 | 核验实际 JSON/毫秒单位/段内基准/全文/容量；缺失时补真实对齐，不按字数估时 | P0（字幕） |
| Qwen/外部对齐 | workflow.timing / alignment_command；production `--timing` | 为新 PCM 生成可靠 cue | 配置缺失；无内置完整对齐器 | 提供真实音频哈希绑定 subtitle-timing；新 TTS 的哈希必须生成后取得，不能预填旧音频 | P0（Qwen长稿） |
| 导演 | 工作流 director=agent/local；独立 production `--director codex/local`；direction / `--direction-file` | 场景/B-roll/高亮规划 | 语义 Agent 未本轮实测，local 字面工程通过 | 注意两个入口枚举不同；可外部 Agent/人工提供 direction，无需额外导演 API Key | P1 |
| 本地渲染 | render.segment_seconds/crf/preset/threads/ffmpeg/ffprobe | 分段CFR/MP4/缓存 | 真实 FFmpeg 工程通过 | 实际媒体上核验输出；未要求继承源码率，CRF 不保证不降码率 | P1 |
| 音乐/音效 | render.audio_mix 的 path/sha256/start/end/source_start/volume/rights/rights_note | 本地混音 | 真 FFmpeg 合成音轨工程通过 | 使用授权足长音频，时间不越界；语义/响度需人工听验 | P2 |
| 字体 | subtitle_style.font | ASS烧录 | 默认 Microsoft YaHei，实际作者字体/字形待验 | 检查已安装字体，查看成片是否 fallback/缺字/遮挡；不能只看配置名 | P1 |
| 任务/错误 | tasks run/status/logs/cancel、timeout、--resume | 执行/观察/恢复 | 本地实际通过；云真实恢复未测 | failed/cancelled 恢复原 task；确认所有进程停止后才处理残留锁；不清收费账本 | P1 |
| 成品自动检查 | final_check result.video、verification.json | 正式 final.mp4 技术发布 | 工程通过，真人成片待测 | 同时检查任务 completed 和 automatic_checks=passed；human_review 仍 pending | P0（验收） |
| 全流程配置 | examples/video/workflow.json、real-suite.json | 统一新文案视频和分级验收 | 待填写模板，不能直接运行；level正文不存在 | 后续创建独立生产配置/脚本，替换真实路径/ID/授权；按分级顺序验收 | P0 |
| 统一诊断 | doctor / lipsync doctor | 就绪反馈 | 可执行但无全链路汇总 | 联合本表人工判定；返回0或find_spec=true不等于端到端ready | P2 |
| dotenv | `.env.example` / 自动 .env加载 | 可选配置体验 | 不存在模板/加载代码；已有 JSON等效模板 | 当前必须用系统/会话环境变量；不新造与代码不匹配的 CREATOR_TTS_KEY 等规范 | P2 |
| 当前包构建 | wheel/独立安装 | 脱离editable复验 | 本轮未构建/重装 | 后续隔离安装新构建，检查4个Skill资源/31Schema/模块/CLI；历史wheel不等于当前复验 | P2 |

## 2. 先做无费用检查

以下命令使用现有解释器，不下载模型、不发送 TTS 或 Agent 推理请求。先在项目根目录执行，避免相对 workspace 和 MuseTalk 路径随 CWD 改变。

```powershell
Set-Location D:\Project_Agent\CreatorDNA
.\.venv\Scripts\python.exe -m creator_dna doctor
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m creator_dna tts capabilities --config examples/tts/qwen.json
.\.venv\Scripts\python.exe -m creator_dna tts capabilities --config examples/tts/minimax-cn.json
.\.venv\Scripts\python.exe -m creator_dna lipsync doctor --config examples/lipsync/musetalk.json
.\.venv\Scripts\python.exe -m creator_dna skills list --workspace workspace --creator creator-001
.\.venv\Scripts\python.exe -m creator_dna tts voices --workspace workspace --creator creator-001
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前预期：pip check 报 click 冲突；lipsync doctor 的 ready=false；tts voices 为空；unittest 162通过、3真实集成skip。通用 doctor 只可发现依赖，不能覆盖各 Provider 的账号验证。

本轮已加载并转录的 small 模型目录为 `C:\Users\EDY\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots\536b0662742c02347bc0e980a01041f333bce120`。后续 prepare/distill 的 `--asr-model` 可指向这个现存目录；迁移机器后应换成新机器实际路径，不能只复制路径字符串。

## 3. 获取 Key 和设置真实环境变量

### 3.1 选择一个实际使用的 Provider

Qwen：在[阿里云百炼获取 API Key](https://www.alibabacloud.com/help/en/model-studio/get-api-key)，确认 Key 所在地域/业务空间与 base_url 匹配。当前模型 `qwen3-tts-vc-2026-01-22` 的北京/新加坡使用范围见[官方指南](https://www.alibabacloud.com/help/zh/model-studio/non-realtime-tts-user-guide)。配置真实字段为 provider/model/base_url/api_key_env；注册 target_model 要与合成模型一致。

MiniMax：在[国内平台](https://platform.minimax.cn/)或[国际平台准备说明](https://platform.minimax.io/docs/guides/quickstart-preparation)登录相应账户并创建 API Key。国内使用 `examples/tts/minimax-cn.json`、变量 `MINIMAX_CN_API_KEY`；国际使用 minimax.json、变量 `MINIMAX_API_KEY`。确认账户余额/权限后再执行真实请求，不需要两端同时配置。

本项目不读取 .env。下面是在**同一个 PowerShell 会话**设置 Key 的示例，输入被隐藏，不把实际 Key 写进命令历史。此片段是用户后续操作示例，本轮没有执行。

```powershell
$ttsSecretInput = Read-Host "输入选定服务商的 API Key" -AsSecureString
# 三选一：按所用配置的 api_key_env 选择赋值行
$env:MINIMAX_CN_API_KEY = [System.Net.NetworkCredential]::new("", $ttsSecretInput).Password
# $env:MINIMAX_API_KEY = [System.Net.NetworkCredential]::new("", $ttsSecretInput).Password
# $env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new("", $ttsSecretInput).Password
Remove-Variable ttsSecretInput

# 只检查存在性，绝不打印 Key
[bool]$env:MINIMAX_CN_API_KEY
```

若在 Windows 用户环境变量界面保存，重新打开终端/重启需要继承该变量的 Codex 进程后再检查。变量设置到另一个已结束的 shell，不会传回父进程。请勿把 Key 写入 JSON、授权说明、报告或命令参数。

`tts capabilities` 验证参数合法性，**不验证 Key**。需要鉴权事实时，后续可在明确允许服务访问后运行 `tts remote-voices --config <实际配置>` 核验账户；本轮未运行。注册/合成和 real-suite 均可能产生费用，不能用 capabilities 的成功替代它们。

### 3.2 真实声音参考与 Voice ID

Qwen 本实现检查 WAV16/MP3/M4A、单声道、至少24kHz、3–60秒、≤10MiB；连续清晰人声/噪声仍需人工检查。MiniMax 检查 MP3/M4A/WAV、10–300秒、≤20MiB。选择真实干净单人样本，优先10–20秒，不用背景音乐/静音或原视频混合声证明复刻效果。参考要求见[Qwen声音复刻](https://www.alibabacloud.com/help/en/model-studio/voice-cloning-user-guide)、[MiniMax上传参考](https://platform.minimax.io/docs/api-reference/voice-cloning-uploadcloneaudio)。

后续在真实授权范围内执行（占位内容必须替换，workspace 必须在源目录外）：

```powershell
# 本地提取/复制，尚不上传；confirmation填写真实授权依据
.\.venv\Scripts\python.exe -m creator_dna tts reference --workspace workspace --creator creator-001 --config <实际TTS配置.json> --source <授权参考声音或视频> --authorize --cloud-processing --confirmation "真实审核人、授权依据、用途及该服务商云端处理范围"

# 使用上一步返回的 reference_id；只有 --run 才实际执行云注册
.\.venv\Scripts\python.exe -m creator_dna tts register --workspace workspace --creator creator-001 --config <实际TTS配置.json> --reference <实际ref-ID> --name <合法音色名称> --run

.\.venv\Scripts\python.exe -m creator_dna tts voices --workspace workspace --creator creator-001

# 注册完成后，用返回的 voice_asset，而非把原始云Voice ID当本地asset ID
.\.venv\Scripts\python.exe -m creator_dna tts synthesize --workspace workspace --creator creator-001 --config <实际TTS配置.json> --voice <实际voice-asset-ID> --script-file <新的纯口播正文.txt> --run
```

Qwen name 前缀1–16位字母/数字/下划线；MiniMax Voice ID 8–256位、英文开头、末尾不能为 `-`/`_`。已有音色走 `tts import-voice --voice-id <实际云ID> --authorize --cloud-processing --confirmation ...`，程序会查询云列表核验；未知/未激活 MiniMax 音色可能不在列表中。MiniMax 注册后需七天内首次合成激活，项目会保存截止时间；[官方生命周期说明](https://platform.minimax.io/docs/api-reference/voice-cloning-clone)。

当前 Qwen fallback 的 review_required 不具备专门的人工批准 CLI，不能手改 JSON 放行。先改善参考重新注册；后续再补审核操作。云注册未知结果应核对列表/控制台，不能盲目重注册。未知 TTS 结果优先检查原任务的 receipt 和账单，使用既有 `tts resolve` 时也要保持请求预算并明确接受额外计费，不以新任务绕过账本。

## 4. MuseTalk 环境与实际模型准备

这是当前 P0 阻塞，不需要口型 API Key。实际适配器只支持本地 MuseTalk v15。部署步骤属于后续环境准备，本轮没有下载、安装或推理。

1. 依据[官方 MuseTalk 仓库](https://github.com/TMElyralab/MuseTalk)取得代码并固定版本，参考本项目 `docs/V3-04-DESIGN.md` 记录的适配版本。不要随意使用上游新 API 替换而跳过兼容测试。
2. 单独建立模型 Python 环境。现有主环境是3.14，不作为已验证的 MuseTalk 环境；Blackwell 要有支持sm_120的Torch/CUDA，并解决对应MMLab二进制依赖。不能直接照抄旧torch2.0.1/CUDA11.8；[PyTorch官方说明](https://pytorch.org/blog/pytorch-2-7/)仅证明软件支持背景，不证明本机安装完成。
3. 按官方权重布局准备 models 下的文件。以 `lip_sync_provider.py:13` 的 MODEL_FILES 和 `:18` 的 CODE_FILES 为项目实际完整性标准。模型文件包括 musetalkV15/musetalk.json、unet.pth；sd-vae/config.json、diffusion_pytorch_model.bin；whisper/config.json、pytorch_model.bin、preprocessor_config.json；dwpose/dw-ll_ucoco_384.pth；face-parse-bisent/79999_iter.pth、resnet18-5c106cde.pth；s3fd/s3fd-619a316812.pth。S3FD 的项目引用来源见 `docs/V3-04-DESIGN.md`。这些大文件全部未在示例目录就绪。
4. 在后续独立配置中填写真实 **绝对** repository/python/ffmpeg/ffprobe。运行时禁止自动联网拉模型，缺缓存就失败；ASR 的 CTranslate2 small 目录不能代替 MuseTalk 所需 transformers Whisper 权重。
5. 运行 `lipsync doctor --config <真实配置>`，必须 ready=true。它会实际检查 CUDA矩阵运算和原生扩展导入。然后再验证5–10秒真人样片、VRAM峰值和耗时；ready=true 仍不能替代推理/人物效果验收。

## 5. 人像审核、视觉证据和授权素材

可用的现存真实库：

`workspace/v3-04/creators/creator-001/runs/video-analysis-184e5d0c3be74773bb6cd557512e8478/library.json`

现有审核表 `workspace/v3-04/portrait-preflight/review.json` 有189项，**人工可用数0**。播放完整候选，真实填写 approved、reviewer、evidence、front_facing、face_clear、mouth_unobstructed、moderate_head_motion、natural_action、quality_score、scene_id、action_key、start_pose、end_pose、safe_start、safe_end；每段需额外0.2秒源帧余量。不得自动将所有项改true。

从真实 reference 生成的静态代理分数不证明身份/嘴无遮挡/自然动作。原片若有烧录标题/旧字幕，口型模块会原样保留；选无包装机位或人工制作干净素材，不能期待配置一个 Key 自动擦除它们。

视觉风格使用 production profile 的 review-template：按真实 source_id/time/frame证据填写人工观察；人物/B-roll占比只表示已审核覆盖，不能外推全部。production config 可输出独立配置，编辑 subtitle_style/font/colors/position/margins/layout/background_asset_id 后由 `production_config` 引用；自动profile不会把自由文本观察直接改成渲染参数。

production assets 导入候选初始 rights=unknown。人工分类/授权，补主题和准确描述；背景必须已有授权素材，当前没有抠像/背景补全或联网素材搜索。每次 production import 返回新清单，下一步使用这个新路径。已有口型成品必须绑定同一整段 canonical PCM 的 audio_sha256/offset，不能把原声片段冒充新口型。

独立 `lipsync consent` 生成 source/audio双哈希的本地授权文件；完整工作流用 `portrait_authorization` 的真实source_sha256/creator_id/authorized/processing=local/scope/confirmation/confirmed_at，并在新PCM生成后自动绑定音频。授权应依据已有真实范围填写；本轮审计不会创建授权事实。

## 6. 字幕对齐和收费前检查

MiniMax 示例开启subtitles；真实回包还须满足项目解析字段（time_begin/time_end或start_time/end_time，毫秒、段内音频基准）。字幕文本要覆盖当前全文，时间合法且不重叠，每个原始单元不能超出配置容量。若服务只返回过长句级区间，仍需更细真实对齐。

Qwen 示例subtitles=false，仅短单句且容量内可接受“实测段边界 coarse”；普通多句长稿不能直接成为字幕。可选：

1. 对当前 canonical PCM 运行外部真实对齐器，生成现有 `subtitle-timing` JSON，通过 workflow `timing` 或 production `--timing` 提供。audio_sha256必须属于当前WAV，不能复用参考原声/合成音频的哈希。
2. 在统一工作流 `alignment_command` 提供 argv 数组，使用绝对可执行/脚本路径，`{request}`/`{output}`占位符由适配器替换。请求包含audio/audio_sha256/text/output；输出按 `src/creator_dna/schemas/subtitle-timing.json`，method如实标forced_alignment/provider/asr/asr_reviewed，所有cue来自实际音频，不能按字数生成假时间。

**当前先TTS、再口型、最后字幕编排。** 在完整运行前安排好对齐工具、候选/授权、素材和生产配置；否则可能已经收费才因后面的配置失败。程序目前只在收费前检查Key存在和口型环境，对候选/字幕没有完整前置门禁。新音频时长和真实回包仍必须生成后核验，但对齐器能否启动、候选是否有人审过、必要文件是否存在应提前核实。

## 7. 组装真实工作流及验收

后续创建自己的配置副本，不覆盖现有示例。字段只使用已存在的 `video-workflow-config`：schema_version=3.0-06、profile、assets；按分支选择speech或tts+voice_asset+lipsync；再填portrait/portrait_authorization、skill_version、quality、max_revisions、director、agent、production_config、timing或alignment_command、render。顶层文件路径相对workflow JSON；MuseTalk配置内repository/python按执行CWD解析，当前用绝对路径最稳妥。

新主题需正确workspace中的已批准Skill；已有script纯正文且quality=false可不用Skill。工作流director枚举agent/local，独立production命令枚举codex/local。local只字面匹配；提供direction可使用人工/外部Agent结果，不算自动语义风格验收。

先真实短样片，再分级：

```powershell
# 只有后续具备全部条件并明确接受真实费用后执行
.\.venv\Scripts\python.exe -m creator_dna video create --workspace <生产workspace> --creator creator-001 --config <真实workflow.json> --script-file <真实新稿.txt> --run

# 准备真实level正文并修改自己的suite；按实测语音时长划分级别
.\.venv\Scripts\python.exe scripts/validate_v3_render.py --real-suite <真实suite.json> --workspace <独立真实验收workspace>
```

Level1=10–20秒、Level2=30–60秒、Level3=120–180秒；前一级失败不得推进。real-suite需要新TTS+真实口型，不能输入现成speech代替。当前examples/video/real-suite.json引用的level1/2/3.txt不存在，必须后续补真实正文。正文字符数不保证时长。

若走opt-in测试，真实变量名称如下（只在未来显式接受调用及费用时设）：

| 测试 | 必需开关/参数 |
| --- | --- |
| TTS | CREATORDNA_TTS_REAL_TEST=1；CREATORDNA_TTS_CONFIG、WORKSPACE、CREATOR、VOICE（全部带CREATORDNA_TTS_前缀） |
| TTS额外复刻 | 再设CREATORDNA_TTS_REAL_CLONE=1、CREATORDNA_TTS_REFERENCE、CREATORDNA_TTS_VOICE_NAME；已有真实授权参考，不能隐式上传 |
| 单段真人口型 | CREATORDNA_LIPSYNC_REAL_TEST=1；CREATORDNA_LIPSYNC_CONFIG、WORKSPACE、CREATOR、AUDIO、LIBRARY、REVIEW、CONSENT、TIMELINE（全部带CREATORDNA_LIPSYNC_前缀）；可选CANDIDATE/DURATION |
| 完整真实视频 | CREATORDNA_VIDEO_REAL_TEST=1、CREATORDNA_VIDEO_WORKSPACE、CREATORDNA_VIDEO_SUITE |

每次检查 task.status、各stage的result和final_check结果，不只看进程退出0；评价命令尤其要读取passed/failed/unable。成片必须有final.mp4/verification.json，并自动检查passed；人物身份、声音自然、口型感知同步、重复动作、B-roll含义、字体/旧包装干扰、作者风格需要人工记录具体秒位置和结论。human_review=pending不能改述为验收完成。

优先顺序：主环境及隔离防护 → 选一个TTS/正式音色 + 独立MuseTalk环境 → 人像审核/真实素材/视觉配置/对齐 → 真实TTS和5–10秒口型 → 切口衔接修复 → Level1/2/3及人工验收 → V2真实盲评/独立包安装。安装、模型下载、业务小修复和真实费用测试均为后续工作，本轮未执行。
