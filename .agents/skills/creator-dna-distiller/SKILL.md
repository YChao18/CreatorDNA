---
name: creator-dna-distiller
description: 从同一创作者的参考素材文件夹或标准化转写稿中提炼口播表达风格，生成有原文证据、可独立复用的 Creator-Skill。用于 CreatorDNA 风格蒸馏，不用于直接创作新选题文案或生成完整视频。
---

# 创作者口播风格蒸馏

将创作者反复出现的表达规律转化为新选题可执行的创作指令。保留创作规律，避免将原稿中的具体事实、品牌、观点或话题当作永久风格。

## 输入与执行方式

- 素材文件夹尚未处理时，通过 CreatorDNA CLI 的 `prepare` 或 `distill` 使用独立媒体模块。不要在提示词中实现 ASR、修改原视频或猜测听不清的内容。
- 通过系统收到标准化 JSON 转写稿时，直接分析请求中的 segments。片段索引从 0 开始；每个素材均有独立 source_id。
- 推理请求已包含完整输入与输出 Schema 时，仅返回对应 JSON。无需读取磁盘、运行命令或调用其他 Agent。
- 读取 [references/analysis.md](references/analysis.md) 的分析准则。在系统推理请求中，这份准则会自动附加。

## 产出

同时输出 `style_profile` 和 `skill_markdown`，遵循请求给出的 Schema。

1. 比较素材之间的共同规律与反例；把稳定风格、话题偶然性及样本不充分的部分区分清楚。
2. 每条规则用可执行指令表达，记录 dimension、confidence 和原文 evidence。quote 必须是指定 segment 的精确连续子串；不能拼接不同片段，不能修正引文标点。无需编造时间戳。
3. 多素材重复出现的规律比单条巧合可信。低样本量、转写噪声、素材重复或题材集中应写入 limitations。置信度是定性判断，不是实测准确率。
4. Creator-Skill 的 frontmatter name 必须为请求规定的 `creator-{creator_id}`；description 应明确何时使用。正文提供具体写作流程、风格选择原则及事实不足时的处理方式。
5. Creator-Skill 必须引用 `[风格规则](references/style-guide.md)`。系统会把 profile 转换成该文件。正文写出关键规则，不只让读者跳转引用。
6. Creator-Skill 只能依赖自身内容和这一包内引用；不引用源视频、转写目录、元 Skill、CreatorDNA CLI、外部 URL 或绝对路径。
7. 创作时迁移钩子、句式和节奏，不复制长段原稿、不伪装为创作者本人的真实经历，不把样本里的数字或案例冒充新话题的事实。

## 人工确认

系统保存为独立版本的草稿。向用户呈现 profile、Skill 和样本局限，人工审阅后通过 `skills approve` 激活。风格一致性由人工换选题、多次生成和对照测试判断，当前元 Skill 不输出虚假的相似度评分。
