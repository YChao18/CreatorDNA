# V3.0-02 工程验证

## 执行结果

2026-10-09 在本仓库 Windows 环境、现有 `.venv` 中执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果 **85 tests，OK，20.472 秒，无跳过**。包含原有 V1/V2/V3.0-01 回归与本阶段新增 17 项测试；没有使用真实云模型、上传媒体或证明创作者风格质量。FFmpeg/FFprobe 和 Pillow 在本机可用，实际视频相关集成测试均执行。

| 覆盖内容 | 实际检查 |
|---|---|
| 正常完成、进度、阶段切换 | 真实写入/哈希核验分块，检查中间百分比、estimated 标识、阶段和时间 |
| 不提供百分比的 provider | 明确标记的合成 provider，仅状态/分块计数，等待中持续更新耗时且 percent=null |
| 状态持久化、日志 | 新 manager 读取同一状态，Schema 校验，结构化日志格式、历史/筛选/CLI |
| 失败、有限重试 | 可重试错误最多 N 次；不可重试立即停止；失败产物不入 output_files |
| 取消 | pending、运行中、重试等待中取消；显式恢复 cancelled；completed 取消不改结果 |
| 超时、进程清理 | 超时和取消停止独立 worker 及实际 Python 子进程；检查子进程已退出 |
| 安全重跑、恢复 | 跳过成功阶段、保留产物和 attempt 历史；文件篡改或不安全阶段拒绝恢复 |
| 真实 FFmpeg | 合成 MP4、原生进度更新、render → final_check、FFprobe 时长和完整解码 |
| FFmpeg 错误/取消/超时 | 真正非法 filter 失败，真实渲染取消/超时不发布部分 MP4，也不启动成品检查 |
| 假成功防护 | 返回零但未产出文件时报告 InvalidArtifact |
| V3.0-01 接入 | 实际分析短合成视频，原素材 SHA-256/mtime 不变；源快照改变拒绝执行 |
| Windows 原子状态写入 | 读写 sharing violation 有界重试；持续拒绝时旧有效状态不变、临时文件清理 |

测试 fixture 是工程合同/状态样本，不代表任何云端服务能力、真实 TTS/口型/字幕效果或创作者视觉/文本风格质量。

## 可查看的实际演示证据

执行：

```powershell
.\.venv\Scripts\python.exe scripts/validate_v3_tasks.py --workspace .\workspace\v3-02-validation
```

最终六场景成功验收，summary 为 `workspace/v3-02-validation/validation-1597313fd70f431c944cec00fc55097b.json`。每次重跑创建新 UUID 任务和独立 summary，不覆盖历史。summary 的 observations 保存真实状态/进度更新；tasks 字段给出状态、日志和输出绝对路径。

| 场景 | 任务 ID | 最终状态/断言 |
|---|---|---|
| 分块写入与核验 | task-2f82cc2e7a85487cb78024983a0973ac | completed，两阶段完成 |
| 真实合成测试图渲染 | task-a54a7ba8329941579fac5c155887adaf | completed，2 秒 320×180 MP4，解码/时长通过 |
| 可重试失败后成功 | task-f9482b0a3b5744cb966e278b3a73e76d | completed，恰好两次，输出仅来自第二次 |
| 达到重试上限 | task-c0243891088f494ba87e235315726c97 | failed，恰好两次，无已完成输出 |
| 尝试超时 | task-1564dec43ecf4b7ba07629855caa4b55 | failed，TaskTimeout，无已完成输出 |
| 两独立 CLI 进程取消 | task-c8ce242a4ab74735af2a43dd3834dc06 | cancelled，执行进程退出码 130，无已完成输出 |

FFmpeg 成片位置：`workspace/v3-02-validation/tasks/task-a54a7ba8329941579fac5c155887adaf/stages/render/attempt-001/outputs/synthetic-demo.mp4`。该任务 status.json 包含 output_files、阶段结果和 artifacts 哈希，logs.jsonl 保留原生进度事件，final_check/attempt-001/outputs/verification.json 保存真实时长、SHA-256 和 decode_ok。

取消场景的 terminal.log 保留运行终端反馈，cli-result.json 是最终结构化输出。异常尝试的 stderr、result.json 和未发布测试文件仍在各 attempt 目录，便于诊断。此前排查过程产生的失败任务也保留在 workspace 历史中；应以这里列出的最终 summary 作为本次验收证据。

`git diff --check` 和 Python compileall 检查通过。旧版本内容和未提交的已有工作保留，没有做提交、删除历史或发布操作。

## 已知边界

当前采用前台单任务编排和独立阶段进程，没有常驻后台调度服务。可在另一终端观察/取消前台任务。多个任务允许独立运行，但未实现全局 GPU/云配额调度。

引擎支持阶段完成检查点，未实现远端 API 真正的断点续算。远端取消和幂等必须由提供方实际保证；本地杀进程不等于云端运算已停止。强制断电后残留锁需要人工确认进程停止，再恢复。Windows 文件共享错误重试有限，永久权限拒绝仍明确报错。用户配置的较长路径仍受本机文件系统和工具限制。

本阶段没有真实云模型测试和真实创作者产品质量验收，也没有提前实现 TTS、口型同步或字幕业务。后续提供方接入协议、稳定幂等键、进度及 FFmpeg 复用示例见 `V3-02-DESIGN.md`。
