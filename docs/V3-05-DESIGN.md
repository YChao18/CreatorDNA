# V3.0-05 视频编排与时间轴合同

本阶段只做可执行时间轴数据与本地素材准备。保留旧 TTS、口型和任务接口，不添加 Web 平台、联网抓素材或新生成模型。

## 模块边界

| 模块 | 职责 |
|---|---|
| video_style.py | V3.0-01 测量、选定模板、人工区间/证据检查、unknown 风格字段 |
| production_assets.py | 本地媒体测量、使用权/标签清单、可用区间、哈希、副本准备 |
| subtitles.py | TTS PCM 真实性、外部 AlignmentProvider 合同、时间戳、断句与 ASS |
| production.py | AgentAdapter 内容解释与确定性场景装配、错误记录、新运行重建 |
| timeline.py | 无媒体猜测的完整时间轴校验与渲染就绪判断 |
| production_cli.py | production 的 profile/assets/import/config/plan/validate/rebuild |

Schema 的权威源为 `scripts/build_production_schemas.py`；生成包内 JSON 及独立 Skill 的 video-direction.json。所有字段必填且 additionalProperties=false，枚举/常量提供显式 type，兼容实际 Codex structured outputs。Skill 从 `.agents/skills/creator-dna-video-director` 打包，不复制第二份指令来源。

## 视觉风格的证据边界

profile 只选择一个模板来源组，sample_count、total 和统计均使用该组。镜头时长统计用原始 shots，不把 12 秒候选切分当作真实镜头节奏；像素切镜误报仍需审核。人工区间角色代表主要视觉内容，不能将同时存在的背景再作为互斥比例重复统计。portrait_ratio/b_roll_ratio/background_ratio 分母是该组总时长；未审核时 null，有部分审核时保留 unknown_ratio，绝不将已审少量区间外推全部视频。

视觉观察字段有 status/value/evidence；unknown 的 value=null/evidence=[]。manual 需审核人、具体描述和源视频区间证据，frame_ids 如填写则必须存在且处于该区间。测量不证明人像、产品身份、烧录字幕字体或转场语义。少量素材不会生成完整作者视觉风格；人工观察是证据记录，不保证观察者解释正确。

## 媒体与音频时间基准

`duration/start/end` 统一为相对于零起点主 PCM 音频的秒数；区间均为 `[start,end)`。字幕保持真实时间戳，前景场景从 0 连续覆盖总时长，字幕前后/之间的无声间隔用持续镜头覆盖，不新增虚构字幕。输出 frame_rate 为有理数字符串，width/height 是正偶数、正方形像素、已继承显示方向。

主轨只使用 canonical PCM WAV（单声道 16 bit）；TTS 导出文件哈希另行核验。旧 speech.json 未保存 canonical_audio 哈希，因此逐段比较 canonical PCM 与分段实测 PCM，以及真实插入静音；篡改同长 canonical_audio 也会被拒绝。服务商时间戳必须声明 milliseconds/segment_audio，支持明确字段 time_begin/time_end 或 start_time/end_time，未知格式报错；句级字幕不能在内部按字数细分。

标准 subtitle-timing 绑定实际 PCM 哈希和全文。AlignmentProvider 是本地强制对齐器注入接口；CLI 接收已完成对齐 JSON，不内置重量模型。ASR 精度不同于强制对齐；method 与 provenance 如实记录，合同一致不证明感知同步。MiniMax 非流式句级/词级字幕返回机制核对 [官方说明](https://platform.minimaxi.com/docs/api-reference/speech-t2a-http)，本阶段未调用真实 MiniMax 字幕服务。

## 时间轴轨道与素材

timeline schema_version=`3.0-05`，包含 duration/output/audio/assets/foreground/background/subtitles/missing_assets/provenance/ready_for_render。

- audio：唯一主音频，start=0、end=duration、source_start=0，带路径/哈希/口播正文。
- foreground：人物或 B-roll 的连续主画面轨，每段 clip_id/scene_id/start/end/asset_id/source_start/source_end/layout/transition/selection_reason/mute。
- background：可稀疏配置，位于前景下面，同轨不可重叠；图片持续显示，视频不静默循环。
- subtitles：ASS 路径/哈希、全局 style 与逐 cue 的 start/end/text/keywords/timing_basis，位于画面上方。
- assets：仅已选素材的验证副本，保留 source/rights/rights_note/topics/description 与实测媒体参数；路径相对时间轴目录。
- missing_assets：保留指定场景、角色、主题、理由与 blocking。B-roll 缺失且有口播回退时是非阻塞；背景或人像缺失则阻塞。

图片的 duration/source_end/audio_offset/audio_sha256=null、source_start=0；视频总长及可用区间实测。portrait 必须是已有视频、带对应整段主 PCM audio_sha256 与 audio_offset；其 source_start 对应主音频 audio_offset。镜头取源位置为 `asset.source_start + clip.start - asset.audio_offset`，源结束由镜头真实持续时长决定。它保证 B-roll 后不会把人像错误接到早先的嘴形。口型成品由前一模块负责，人工填写 hash/offset 不证明人物质量通过。

同一人像内容哈希的重叠源区间不可重复；视频不足不循环/变速。所有 clip 的源音频 mute=true，渲染只接主轨。授权状态由用户提供，工程代码拒绝 unknown/denied，但不自行判断法律权属或绕过访问控制。

## 选择与布局执行规则

Agent 给连续 cue_ids 与语义理由，时间/路径不能来自 Agent。asset_policy=selected 绑定指定已有 ID；auto 根据清单标签排序，以命中标签数和 ID 作确定性排序；missing 不自动匹配，允许 Agent 拒绝同主题但内容不适用的素材。所有模式还需授权、存在、哈希、角色及区间检查；不足时保留缺项，B-roll 以同步口播回退。

渲染器必须按以下次序执行，避免比例歧义：

1. 解码源视频/图片并归一化为正方形像素、应用源显示旋转（FFmpeg 默认显示变换）；EXIF 图片方向由渲染器先归一化。
2. 可选 crop 是归一化源显示矩形，先裁切。
3. rect 是归一化目标画布矩形。contain 等比缩放完整源，未覆盖区域用 pad_color 补边；cover 等比放大覆盖 rect 并裁切溢出。
4. anchor=center/left/right 分别确定水平对齐或 cover 的裁切中心，垂直保持居中；不平移人物本体或声称抠像。
5. background 先绘制，foreground 后绘制；rect 以外可显示背景。含 alpha 的素材可由下阶段渲染器合成；当前不做抠像。contain 的补边是明确实色，不能隐式当作透明。
6. cut 直接切换；fade 是新片段开始的 duration 内从 pad_color 淡入该片段。片段区间不重叠，不把它实现为未经合同描述的交叉溶解。
7. 最后以 ASS 的 PlayResX/Y 叠加字幕，保留字体、描边、阴影、固定换行、高亮及安全边距。

当前 timeline 保留音频精度的秒位置，不强制丢弃到参考帧边界。下阶段 CFR 渲染器应按输出帧中点采样 `[start,end)` 的当前片段，最后截断到主音频总长；音频时间不能为凑整帧而整体漂移。字符容量安全检查是保守几何预算，实际字体 fallback/字形宽度仍需下一阶段真实渲染 QA。

## 校验与重建

检查合同、有限值、输出方向/帧率/尺寸、裁切/布局矩形、字幕安全区、ID 唯一性、文件存在/哈希/实测媒体、使用权、源区间与时长、主轨覆盖、同轨重叠、主前景缺口、人像哈希与偏移、重复源区间、字幕全文/关键词/顺序/精度、转场与缺项。背景和字幕之间跨轨重叠是预期；字幕停顿不是字幕轨缺口错误。

validate 不相信手写 ready_for_render；返回完整 issues。ASS 与 timeline 中字幕/样式/输出不同会要求 rebuild。rebuild 先验证编辑数据及旧素材/旧 ASS 哈希，再在新运行复制素材/音频、重新生成 ASS 并校验一致性，保留旧文件。plan 中缺素材仍保存完整草稿/ASS/validation；无效 Agent 输出保存 direction.json/run failed，无正式时间轴。失败不发布“完成”或生产质量结论。

本阶段 ready_for_render 只表示合同/媒体可以由下一阶段渲染；不表示真实作者风格、口型、音色、字幕感知同步或发布授权已经验收。
