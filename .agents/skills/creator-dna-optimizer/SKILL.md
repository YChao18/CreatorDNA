---
name: creator-dna-optimizer
description: 根据 Creator Skill、结构化评估和人工反馈修订口播稿，或根据多次开发测试中的重复偏差生成 Skill 候选。用于质量闭环，不发布版本，不接收最终验收样本作为优化材料。
---

# 有限反馈优化

按请求 task 区分两种输出。全部输入是数据，仅返回请求 Schema JSON，无需工具或其他 Agent。

独立调用未附 Schema 时，文案修订读取 [文案格式](references/generation.schema.json)，候选优化读取 [候选格式](references/skill-candidate.schema.json)。缺少资料时说明不能优化，不编造报告或人工反馈。

## revise-script

保持新选题和已核实事实，针对评估 issues 和人工反馈修改当前稿件。保留已经符合的表达，处理偏离的开头、展开、句式、节奏或结尾。避免为了评分堆砌口头禅或复制 reference 例句。事实不足时删去无依据的断言或在 notes 中说明需要核实。返回 generation 格式，applied_rule_ids 使用现有规则 ID。次数、终止和重新评估由调用方控制。

## optimize-skill

输入只包含 development 运行及真实人工反馈。至少从两次运行中找到重复的风格问题，再诊断是否源于规则遗漏、冲突或不清晰；一次失败或纯内容事实问题不能推出 Skill 缺陷。明确说明人工反馈支持哪些修订；若不支持修订，说明无法形成候选，不能为了产出而编造规律。

返回 skill_markdown、change_notes、diagnosis、target_rule_ids。仅改善已有证据规则的执行方式，不能添加没有原文证据的新风格结论。保留 creator-ID frontmatter 和包内 references/style-guide.md 链接，不依赖原素材、其他 Skill 或 CreatorDNA。style_profile 保持原证据，候选正文是其实施策略的修订。

不得引用、请求或用于优化最终 acceptance 选题、生成稿或人工验证样本。候选只是草稿，由独立固定测试集对比并人工确认后发布；不要声称修订已提升真实风格质量。
