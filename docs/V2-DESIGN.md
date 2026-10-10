# V2.0 增量设计与任务

## 现状（仓库检查）

V1.0 为 Python 3.10+ CLI，仅依赖 jsonschema、PyYAML；ASR 为可选依赖。Workflow 通过 AgentAdapter 调用已登录的 Codex CLI，Meta-Skill 通过包资源加载。SkillManager 已有不可覆盖的版本目录、文件哈希、草稿确认和手动修订。Workspace 提供原子写入、每创作者锁和失败记录。25 项 unittest 使用合成 FixtureAgent 验证工程合同；另有真实 FFmpeg 接口测试。现有生成阶段不读取原素材，继续保持此约束。

## 增量边界

- 新增 `.agents/skills/creator-dna-evaluator` 和 `creator-dna-optimizer`，打包为资源；评估与优化沿用 AgentAdapter，无新模型服务。
- `evaluation.py` 负责配置、证据核对、确定性通过判断和报告；`quality.py` 编排评估、有限修订、追加人工反馈、批量任务和候选优化。
- 扩展 SkillManager：补齐来源/父版本/变更说明/测试关联，版本差异、回滚、候选发布门禁。旧 V1 包无需迁移；旧 approve/revise 继续可用，V2 自动优化候选及其后代必须经过门禁。
- 新增 Schema：评估响应、配置、报告、反馈、固定测试集、候选响应、批量结果、修订历史。所有模型结果先保留再校验。
- 所有新增数据置于 `workspace/creators/<id>/runs` 和 Skill 元数据；不复制媒体，不改源文件。UUID 运行目录防冲突。

## 质量语义

AI 提供 0–5 主观评分、可核对引文、问题和建议；程序根据配置判定 passed/failed/unable。风格、内容质量、原创性风险分别报告，无综合相似度。评估仅使用已核验的 profile 证据及包内容：没有完整原稿时只能评估已提供证据的复制风险，必须注明局限。缺证据、Agent 失败、无效输出不能算低分或通过。

人工反馈追加保存，支持覆盖状态/维度或补充判断；原始 AI 报告不修改。修订重新评估时传入适用反馈；人工确认稿件状态不自动构成版本验收。

批量按固定 case ID、类别、重复序号保存结果；一个子任务失败不阻止其他任务。测试集标明 development/acceptance。候选优化仅接收 development 运行及人工反馈；版本比较要求相同选题/配置/重复次数。发布需 acceptance 对照、候选全部通过、风格与质量不退步、无训练选题重叠，再显式人工确认。人工盲评是独立产品质量验证，AI 门禁不替代它。

## 开发清单

- [x] P0：Evaluation/Optimizer Skills、Schema、证据验证、报告、有限修订、中断、人工反馈。
- [x] P1：版本记录/差异/回滚/关联、批量部分失败、同集比较、候选门禁。
- [x] P2：沿用每创作者隔离，增加列表入口。
- [x] 回归与新增工程测试、中文 CLI 文档和可执行示例。
- [x] wheel 资源校验。
- [x] 独立真实 Agent 接入验证（合成 demo，7 次真实调用）；真实创作者人工盲评保留为待验收。
