# V2.0 实现与验证记录

日期：2026-10-09（Asia/Shanghai）。Python 包版本：0.2.0，产品版本：V2.0。

## 已实现与文件

| 能力 | 实现 |
| --- | --- |
| 独立评估/优化指令 | `.agents/skills/creator-dna-evaluator/`、`creator-dna-optimizer/`，随 wheel 打包 |
| 评估格式、引文检查、配置判断、JSON/Markdown | `src/creator_dna/evaluation.py`、`schemas/evaluation*.json` |
| 有限修订、复评、中断、人工覆盖 | `quality.py`、反馈与修订历史 Schema |
| 批量任务、类别汇总、同集版本对照 | `quality.py`、dataset/batch-report Schema |
| 版本来源、父版本、变更、测试关联、差异、回滚、候选门禁 | `skills.py`、skill-metadata Schema |
| CLI、安装资源与版本 | `cli.py`、`pyproject.toml`、`__init__.py` |
| 设计、使用与独立真实接入 | `V2-DESIGN.md`、中文 README、`examples/v2/`、`scripts/validate_v2_agent.py` |

没有新增运行依赖，没有更换 AgentAdapter/ASR 接口，没有添加 Web、数据库、视频制作或社交发布。原始素材、已发布 demo 包和已有运行未覆盖。

## 工程验证

使用项目虚拟环境执行 `python -m unittest discover -s tests -v`：49 项全部通过，其中 25 项为 V1.0 回归，24 项为新增 V2.0 测试。

覆盖评估 Schema、缺字段/错引文/错规则/重复维度拒绝采用、无资料无法评估、独立原创性失败、提前通过、修订上限、Agent/自定义 Provider 错误、用户中断、人工视图追加、错误反馈关联、人工覆盖阻止错误优化、批量部分失败/部分结果保留、相同测试集比较、统计与实际子任务核对、选题重叠与质量下降拒绝发布、模型固定、草稿后代门禁、旧元数据、CLI 分发、版本差异和回滚。

工程 Fixture 明确标注为合成响应，不调用真实 AI，不构成风格质量证据。真实 FFmpeg 提取接口回归也通过。

两份新 Skill 使用 skill-creator 的 quick_validate 检查通过（Windows 使用 `python -X utf8`）。生成 `creator_dna-0.2.0-py3-none-any.whl`，在隔离导入模式下从 wheel 读取三个 Skill 的 SKILL.md 和所有 Schema，并校验 Schema；不依赖当前工作目录发现资源。

## 真实 Agent 接入验证

执行命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/validate_v2_agent.py --source-workspace workspace --creator demo --batch
```

使用 V1.0 已真实蒸馏的 demo/v001 包副本。该包的来源是 `examples/materials/` 三份**合成转写**，本次初稿及选题也为合成演示；没有真实创作者样本。

隔离目录：`workspace/integration-e89964bd6bca451487a7e67c56182fd2/`。

共 7 次真实 Codex CLI 调用，无 Fixture、固定分数或模拟模型输出：

| 链路 | 真实观察 |
| --- | --- |
| 初稿评估 → 修订 → 复评 | 第一次 failed，修订后 passed；共 3 次调用，两个稿件及两个评估完整保留 |
| 两个固定选题批量生成/评估 | 共 4 次调用，完成 2/2；AI 配置状态均为 passed，unable=0 |
| 原包保护 | SKILL.md、style-guide.md、profile 与 metadata 的前后哈希一致 |

初稿质量运行：`quality-f42e43597ee346708a5584f71ab30387`。

批量运行：`batch-ec803328b0b14f62ac7c2da07fe3141a`。

`integration-summary.json` 保存调用数、状态、原包保护结果和 `real_creator_quality_verified=false`。这些 passed 是这次模型主观评估在默认阈值下的判断，不能解释为真实风格质量验收。没有伪造人工反馈，没有发布新版本。

## 兼容选择与限制

- V1.0 prepare/distill/generate/list/approve/revise/export 保留，已有 1.0 元数据无需迁移。V2 新版本元数据为 2.0；独立 profile 合同仍为 1.0。
- 普通人工修订/蒸馏确认保留 V1.0 人工审核路径，不宣称经过 V2 测试。自动优化候选及后代必须满足独立 acceptance 对照和人工确认。草稿继续修订时保存直接父版本，同时保留已确认的比较基线。
- 全文原创性、事实准确性、规则解释及语义相近的选题划分不能仅靠当前校验保证；程序校验引文的存在，不校验 AI 推断的真实性。
- 自动优化需要原蒸馏运行的转写快照；媒体无需保留，但删除该快照后必须恢复才能重新核证据。
- 默认模型只适合日常运行；发布对照要求明确固定模型 ID。相同配置也不保证模型输出完全可复现。
- workspace 采用本地文件与每创作者互斥锁；不支持跨主机任务协调。批量串行运行，进程被强制杀死后可能需人工清理残留锁。
- 非真实风格数据上的候选优化/版本发布仅通过工程测试验证。没有真实人工反馈时，本次不会运行真实 Skill 级优化和确认发布。

## 尚待产品质量验收

获取同一真实创作者的稳定样本，准备不用于优化的最终验收选题集及人工验证样本。采用匿名随机编号隐藏版本，记录每稿人工风格评分（1–5）、可直接使用与否、修改分钟数、复制和漂移问题，再解盲统计均值、可用率及修改量。

当前**尚未验证**真实创作者风格平均 4/5、80% 可用率、跨选题稳定性提升、人工修改工作量下降或真实视频 ASR 准确率。这些是后续用真实材料验证的目标，不是自动化测试结论。
