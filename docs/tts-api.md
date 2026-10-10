# V3.0-03 云端 TTS 接入审计

核对日期：2026-10-09。本文记录官方接口与本适配器的实际支持边界；查阅文档不等于已通过真实账户验证。

## 阿里云百炼 / 千问

- [声音复刻 HTTP API](https://www.alibabacloud.com/help/zh/model-studio/voice-clone-design-http-api)：使用 `qwen-voice-enrollment`，`POST /api/v1/services/audio/tts/customization`。创建 `input.action=create`、`target_model`、`preferred_name`、`audio.data`（本地声音用 Data URL）。管理用 `list` / `delete`；千问此接口不支持详情查询或更新。本实现保留返回的降级标记，降级音色需要审核。
- [声音复刻指南](https://www.alibabacloud.com/help/en/model-studio/voice-cloning-user-guide)：音色绑定创建时的模型。千问参考支持 16-bit WAV、MP3、M4A；≤60 秒、≤10 MB、单声道、≥24 kHz，至少 3 秒清晰连续语音；推荐 10–20 秒。机器检查格式、时长、大小，授权人还须人工核验单人清晰讲话、噪音和停顿。闲置一年可能清理，列表可用于核对存续。
- [Qwen-TTS 合成 HTTP API](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-api)：本实现使用 `qwen3-tts-vc-2026-01-22`，`POST /api/v1/services/aigc/multimodal-generation/generation`，请求 `input.text` / `input.voice`；同步返回 `output.audio.url`，下载地址有效 24 小时。每段最多 600 字符。此 VC 模型不接受本适配器的数值语速、情绪或字幕参数，非默认请求返回 `Unsupported`；不把 instruct 模型的能力混入 VC。
- [限流](https://www.alibabacloud.com/help/en/model-studio/rate-limit) 与 [错误码](https://www.alibabacloud.com/help/en/model-studio/error-code)：该 VC 合成模型文档列出 180 RPM；仍以账户与地域实际额度为准。本实现只自动重试 HTTP 429 或明确的 Throttling 配额拒绝。鉴权、内容错误、未知 5xx/超时不自动重复计费。

示例默认北京旧域名 `https://dashscope.aliyuncs.com/api/v1`；官方说明旧域名仍有效。新加坡可用 `https://dashscope-intl.aliyuncs.com/api/v1`。也支持文档推荐的 `https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api/v1` 和 `https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com/api/v1`。地域、业务空间及 API Key 必须匹配；更换端点会形成独立音色 scope。

## MiniMax

- [同步 TTS](https://platform.minimax.io/docs/api-reference/speech-t2a-http)：`POST /v1/t2a_v2`；已核对的模型为 `speech-2.8-hd/turbo`、`speech-2.6-hd/turbo`、`speech-02-hd/turbo`、`speech-01-hd/turbo`。请求不足 10,000 字符；本实现每段上限 9,999，默认 500。使用非流式 hex 音频并校验完成状态；支持 WAV/MP3/FLAC、8/16/22.05/24/32/44.1 kHz、语速 0.5–2。情绪按模型声明，`fluent/whisper` 仅对 2.6 系列开放。字幕打开时下载原始句级 JSON，保留毫秒与段内时间基准，不推导词级时间。
- [参考声音上传](https://platform.minimax.io/docs/api-reference/voice-cloning-uploadcloneaudio)：`POST /v1/files/upload` multipart，`purpose=voice_clone`，`file` 字段；MP3/M4A/WAV，10–300 秒，≤20 MB。
- [克隆](https://platform.minimax.io/docs/api-reference/voice-cloning-clone)：`POST /v1/voice_clone`，传真实上传返回的 `file_id` 与用户指定 `voice_id`。ID 为 8–256 字符、英文开头，允许字母数字和 `-_`，末尾不可为 `-_`。本实现不提交预览文本，注册与合成分开。未首次使用的克隆音色需在七天内使用，记录 `pending_activation` 和截止时间。
- [音色列表](https://platform.minimax.io/docs/api-reference/voice-management-get) / [删除](https://platform.minimax.io/docs/api-reference/voice-management-delete)：`POST /v1/get_voice` 与 `/v1/delete_voice`，`voice_type=voice_cloning`。尚未首次合成的克隆音色不出现在列表，不能据此宣称注册失败。
- [异步创建](https://platform.minimax.io/docs/api-reference/speech-t2a-async-create) / [查询](https://platform.minimax.io/docs/api-reference/speech-t2a-async-query)：服务商支持 `/v1/t2a_async_v2` 与 `/v1/query/t2a_async_query_v2`；直接 text 上限 50,000，文件模式可达 1,000,000 字符，状态 Processing/Success/Failed/Expired。本阶段选择短段同步模式，Capability 的 `async_synthesis=false` 指**本适配器**未实现异步，`query_task` 返回 Unsupported，不伪造任务 ID。
- [限流](https://platform.minimax.io/docs/guides/rate-limits)：T2A 与声音克隆列出 60 RPM，未列出可假定的 TTS 连接并发上限。单任务串行、请求间隔默认 1.1 秒，账户还被其他客户端使用时仍可能限流。
- [错误码](https://platform.minimax.io/docs/api-reference/errorcode)：只对 1002/1039/1041/2045 明确配额拒绝有限重试；1004/2049 鉴权、1008 余额、2013 参数、20132 音色/样本、2037 时长、2039 重名、2042 无权限须修正；1000/1001/1024/1033 等结果不明确时保留账本并停止。

默认国际端点 `https://api.minimax.io`；[国内当前官方合成文档](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)与[克隆文档](https://platform.minimax.cn/docs/api-reference/voice-cloning-clone)使用 `https://api.minimax.cn`，对应独立配置 `examples/tts/minimax-cn.json`，应使用匹配账户。原文档站 `platform.minimaxi.com` 已重定向到 `platform.minimax.cn`；本实现只允许核对过的当前 API 域名。请求不跟随带鉴权的重定向。

## 工程约束与扩展

`tts.py` 提供 Provider Protocol、Capability、Receipt、TTSError 与配置校验；`tts_providers.py` 仅处理官方网络协议；`tts_audio.py` 处理确定性的分段和媒体；`voice_assets.py` 管声音资产；`tts_workflow.py` 处理任务账本；`tts_cli.py` 提供入口。

增加服务商时实现协议并加入 `make_provider` 工厂和配置允许值，声明原生格式、模型、参数限制、音色/时间戳能力；添加官方协议 Mock 测试与配置示例。不要向不支持的模型传入“通用”参数。输出格式/采样率与云端原生能力分开，必要时由本地 FFmpeg 统一。

音色隔离键包含服务商端点、凭证**环境变量名**和模型；不保存、哈希或输出 API Key。一个环境变量名应始终指向同一账户；换账户请使用新环境变量名及重新导入音色。注册的未知结果要先核对云端列表/控制台，再导入实际存在的 ID。MiniMax 上传文件保留其 file_id，不自动删除云端样本；需要在服务商控制台按其数据策略管理。

同任务/阶段只执行一次引擎尝试，Provider 请求预算默认每段两次、最多三次，包含恢复历史。网络超时或进程被杀时不能判断是否已计费，账本保持 inflight/outcome_unknown；恢复不重发。已取得 Receipt 时只重新下载或处理，不重新合成；已完成 WAV 校验哈希后复用。签名下载链接仅存任务 checkpoint、不进入进度日志或成品报告，应将 workspace 作为私有运行目录。
