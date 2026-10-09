# CreatorDNA V1.0

把同一创作者的参考素材文件夹，蒸馏成独立可复用的口播风格 Skill，再用新选题生成原创口播稿。

采用双层 Skill：仓库内固定的 `creator-dna-distiller` 元 Skill 负责分析和生成；每个创作者的 Creator-Skill 负责后续文案创作。V1.0 不包含自动评价、视频制作、数字人、配音或 Web 产品。

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

项目已建好用于后续放置真实视频的目录：

```text
materials/
  creator-001/     第一位创作者的一组参考视频（当前为空）
  creator-002/     第二位创作者的一组参考视频（当前为空）
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

采用单仓库、模块化单体；当前模块全部有实际用途，暂不搭建未来视频模块的空框架。

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

测试覆盖文件夹处理、证据真实性、缓存失效、版本隔离、失败不发布、独立 Skill 生成及输入文件保持不变。AI 合同测试使用明确标记的假适配器，不能证明真实风格质量。工程检查可以保证证据引用存在，不能自动判断结论是否正确；第五步仍由人工做多选题、多次生成和对照测试。

`examples/materials/` 是为演示命令制作的合成转写样本，不是真实创作者素材。它们可用于从文件夹到文案的模型接入测试：

```powershell
.\.venv\Scripts\creator-dna.exe distill --input .\examples\materials --creator demo --asr none
.\.venv\Scripts\creator-dna.exe skills approve --creator demo --version v001
.\.venv\Scripts\creator-dna.exe generate --creator demo --topic "为什么整理工作文件比购买新工具更有用"
```

如果强制结束进程，可能留下 `.lock` 文件。确认该创作者已没有任务运行后再删除该锁；一般失败会自动释放锁。
