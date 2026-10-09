# V1.0 首次实现验证

验证日期：2026-10-08（Asia/Shanghai）。实现版本：0.1.0。

## 已完成

- 模块化 Python CLI：文件夹扫描、转写预处理、元 Skill 蒸馏、创作者 Skill 保存与确认、新选题口播稿生成。
- 原素材只读；每创作者隔离的 workspace、转写缓存、原子写入与运行记录。
- JSON Schema 校验、原文证据校验、重复内容提示、高置信度跨素材检查。
- Skill 草稿、激活、修订版本和独立导出；生成阶段不读取转写、原视频或元 Skill。
- 超时与中断保留请求和日志，停止本次 Codex 进程树，并释放创作者锁。
- `materials/creator-001/`、`materials/creator-002/` 是按用户要求创建的空目录，供后续放入真实素材。素材目录不提交到 Git。

## 工程检查

25 项 unittest 通过，包含缓存复用与失效、证据错误拒绝发布、跨创作者隔离、Skill 完整性、版本确认、源文件哈希不变、删除原素材后的生成、失败和中断记录、超时终止、SRT 时间戳、真实 FFmpeg 音频提取、导出文件范围和 CLI 错误反馈。

元 Skill 与真实模型生成的 demo Creator-Skill 均通过 skill-creator 的 quick_validate 检查。可安装 wheel 已构建，并验证 wheel 内能读取元 Skill 和 JSON Schema 资源。

## 真实 Codex 接入

本机 Codex CLI 0.146.0，使用现有认证与模型配置。输入是 `examples/materials/` 中明确标记的 3 份合成转写文本，没有真实人物的视频、声音或身份素材。

蒸馏运行：`distill-2c8280ab806e4f7a94962d7f53a6de65`。

生成 Skill：`workspace/creators/demo/skills/v001/`。真实模型生成 7 条有原文证据的风格规则，包含钩子、结构、语言、文本节奏、情绪和结尾，并注明样本与体裁局限。证据及格式校验通过，检查后用于演示生成。

同一份 Skill 已用于两个不同选题，均完成真实模型调用并保存结果：

| 选题 | 输出 |
| --- | --- |
| 为什么整理工作文件比购买新工具更有用 | workspace/creators/demo/outputs/generate-523787d109894c2f8bb56d375f2eee24.md |
| 为什么睡前把明天要用的东西放在一起能减少忙乱 | workspace/creators/demo/outputs/generate-5b4d91db610f4eefbf8c3ad5181195bb.md |

这些结果证明了当前环境下模型接入与工作流能贯通；不证明真实创作者风格复现的一致性或质量。

## 留待真实素材验证

用户当前还没有参考视频。faster-whisper 在本机可导入，FFmpeg 提取已验证，但没有用真实口播视频验证 ASR 准确率与从视频开始的完整链路。素材准备好后选择一个创作者子文件夹进行蒸馏，人工审阅并确认生成的 Skill，再开展第五步的新选题对照和多次生成测试。

V1.0 不自动计算风格相似度，也不输出未经评测的效果承诺。
