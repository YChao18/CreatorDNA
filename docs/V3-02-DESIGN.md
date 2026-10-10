# V3.0-02 统一任务引擎

## 边界与兼容

继续使用 Python 标准库、现有 Workspace 原子写入及 JSON Schema；不引入数据库、Redis、Celery、消息队列或 Web。保留全部 V1/V2/V3.0-01 命令、独立 Creator-Skill 和已有运行目录。新增 `tasks` 命令，旧流程不强制迁移。

六个稳定阶段 ID：`material_analysis`、`tts`、`lip_sync`、`composition`、`render`、`final_check`。它们是任务协议中的阶段标识，不是已实现的视频生产链。当前只有素材分析适配器、明确标注的本地分块工程演示和 FFmpeg 合成测试图渲染/检查可执行。TTS、口型、字幕业务没有占位执行器。

## 模块与执行

| 模块 | 责任 |
|---|---|
| tasks.py | TaskManager、单写者锁、状态机、心跳、超时/取消、有限重试、完成检查点 |
| task_worker.py | StageExecutor Protocol、StageContext、独立执行器进程、尝试结果 |
| task_progress.py | 有依据的计数进度及 FFmpeg 原生进度块解析 |
| task_executors.py | 真实本地工程演示、FFmpeg 适配器和 V3.0-01 分析接入 |
| task_cli.py | 终端展示、创建/运行/观察/历史/日志/取消 |
| schemas/task-status.json | 持久化状态合同；权威生成器 scripts/build_task_schema.py |

一个任务由一个前台 CLI 进程负责，阶段依次执行，每次尝试启动独立 Python worker。执行器以 `module:function` 注册并在新进程中导入；无需线程共享或 pickle 业务对象。每个尝试的事件文件由 worker 单独写入，parent 增量读取完整 JSONL 行，统一写日志与状态。没有管道缓冲导致 FFmpeg stderr 阻塞的问题：stderr 写文件、stdout 实时读取原生进度。

任务锁阻止同 ID 并发执行；不同任务拥有独立目录。取消命令只写原子取消请求，运行状态由唯一执行者更新，避免多个进程相互覆盖。pending 取消在可取得任务锁时立即落为 cancelled。取消运行任务需等待下一次轮询和进程树清理，以最终 cancelled 状态为准。

阶段 timeout 包含 worker 启动、业务执行和输出哈希校验；重试等待独立计时。父进程用 monotonic 时钟更新耗时和状态，即使执行器完全沉默也有心跳。Windows 用 taskkill `/T /F` 终止 worker 子树，POSIX 用独立进程组；Ctrl+C、超时和取消都走清理路径。本引擎不能保证远端 API 已停止计费或运算：接入模块需使用服务商实际支持的取消接口和幂等键，且报告其局限。

## 进度语义

`progress` 同时存 completed/total/unit/percent/estimated/basis。百分比由 completed/total 计算，total 不明时 percent=null；已经完成 N 分块但总数不明时只展示计数。时间只用于 elapsed_seconds，不参与百分比。无进度 API 的云任务用 `ctx.report` 更新服务状态和 job ID，并显示心跳耗时。

FFmpeg 使用 `-progress pipe:1 -nostats`。解析 out_time_us，兼容 out_time_ms（名字含 ms，实际也按微秒解析）和 HH:MM:SS 时间；按目标媒体时长计算百分比，保证单次尝试不倒退并限制在 0–100。目标时长未知时只有输出秒数。`progress=end` 不代表阶段成功，仍须退出码正确和输出文件检查。

运行中整体百分比仅在当前阶段提供测量百分比时给出，使用各阶段等权重并明确 `estimated=true`；它不是按耗时加权，也不是剩余时间预测。当前工作总量未知时整体 percent=null，但保留已完成阶段数。全部阶段成功并完成文件校验后整体为真实 100%。阶段重试会重置当前尝试进度，attempt 及历史错误解释这种回退。

V3.0-01 的来源数和采样请求数只覆盖部分工作，不能代表包括全片解码、哈希和汇总的整个素材分析阶段。因此任务适配器显示这些子操作的真实计数和状态，但整个阶段的 percent 保持 null，直到完成。旧 analyze-video 的分类和质量局限不变。

## 文件布局与安全重复执行

```text
workspace/tasks/task-<UUID>/
  task.json                               固定计划、参数、provider handler、素材 SHA-256 快照
  status.json                             原子更新，版本 3.0-02，包含阶段及尝试历史
  logs.jsonl                              timestamp/task_id/level/stage/attempt/event/message/data
  cancel.json                             用户取消请求（恢复时显式清除）
  .lock                                   唯一执行者 PID 与创建时间
  stages/<stage-id>/attempt-001/
    request.json                          本次参数、输入检查点、前次尝试、超时
    events.jsonl                          worker 原始进度和日志事件
    stdout.log / stderr.log               执行器输出和错误 traceback
    ffmpeg.stderr.log                     如调用 FFmpeg，保留其完整 stderr
    result.json                           completed metadata 或明确 error
    outputs/                              该尝试的输出；失败文件不登记为已完成结果
```

stage status 的 artifacts 记录路径、大小和 SHA-256。只有执行器成功、结果可序列化、输出无链接/越界且完成哈希后，阶段才成为 completed 检查点。结果从不写到共享成品文件名；不同任务、不同尝试不会覆盖既有输出。任务失败时已完成阶段仍可复用；output_files 只列已完成阶段文件，不代表失败任务已产出经过全流程验收的成片。

素材分析任务调用原流程，但使用短的 attempt-local 独立分析目录，并关闭 publish_latest。它不替换用户既有 `creators/<creator>/video-assets/latest.json`。创建时记录只读素材快照，执行前验证列表/哈希，分析结束再次核验；内容改变时要求创建新任务。批次中部分文件失败遵循 V3.0-01：执行成功但 result.analysis_status=partial、保留失败计数、原因与 warning 日志。调用者应同时检查阶段业务结果。

自动重试仅限 retryable 错误且阶段显式 retry_safe=true，max_attempts 在 1–10 内。默认一次。不可重试错误立即结束；所有失败尝试、stderr 和最后错误都保留。`--resume` 是人工发起的新一次有限执行，最多再次尝试 max_attempts 次；attempt 编号累加，自动重试次数不无限扩展。

恢复先重核已完成 artifacts 哈希，再跳过 completed 阶段；未完成阶段必须显式声明安全重复执行，并从新尝试目录重跑。它不是从某一媒体帧或远端神经网络计算中断处续算。previous_attempts 保留外部 job ID/checkpoint 文件的定位信息，未来执行器可按服务商真实能力复用。普通重复执行 completed 任务只校验并返回，不改产物。

进程被强制关闭或机器断电时可能留下 running 状态和 .lock；不会凭等待时间自动宣称失败或自动偷锁。确认 runner_pid、worker_pid 及其子进程全部停止后，人工移除该任务的 .lock，再执行 `tasks run --resume`。恢复时为旧的 running 尝试补记 Interrupted。不要编辑 task.json/status.json 或把未完成 partial-library.json 当作可用检查点。

## 后续模块接入

先在可导入模块实现 StageExecutor；示意接口（尚未实现的 TTS 业务不在此处模拟）：

```python
def tts_stage(ctx, params):
    # service 必须由该模块显式构造；不可依赖父进程中的闭包或对象。
    service = configured_tts_provider()
    ctx.report("提交语音合成", current_object=params["script_id"], basis="provider accepted request")
    job = service.submit(params["text"], idempotency_key=ctx.idempotency_key)
    # 保存真实 job ID；下一次尝试可从 ctx.previous_attempts 定位并询问服务状态。
    write_json(ctx.attempt_dir / "provider-job.json", {"job_id": job.id})
    ctx.log("服务已接收任务", provider_job_id=job.id)
    ctx.check_cancelled()
    # 无总量时不传 total；已知分块总数时 completed/total 必须来自真实服务结果。
    audio = service.wait_and_download(job, destination=ctx.output_dir / "speech.wav")
    verify_audio(audio)
    return {"provider_job_id": job.id, "audio": "speech.wav"}
```

注册并创建任务：

```python
from creator_dna.tasks import TaskManager, stage

manager = TaskManager("workspace", executors={"my_tts": "my_project.providers:tts_stage"})
task = manager.create([stage("tts", "my_tts", {"script_id": "draft-001", "text": "..."},
                             timeout=600, retry_safe=False)], creator_id="creator-a")
manager.run(task["task_id"])
```

没有真实服务幂等保证时维持 retry_safe=false，不自动重试或恢复提交。确认保证后可启用并设置 max_attempts；用 StageFailure(message, error_type, retryable=True) 标记有限可重试错误。幂等键为 task_id:stage_id，同一阶段多次尝试稳定，不应把 attempt 纳入提交幂等键。

口型同步、字幕编排分别注册到 lip_sync/composition；`ctx.inputs` 只携带之前成功阶段的 artifacts 和结果 metadata。可单阶段任务运行，或由后续工作流声明实际已有执行器组成的计划。渲染模块可复用 task_executors.run_ffmpeg(ctx, command, target_duration)，参数必须是 argv 数组，输出只写 ctx.output_dir，真实目标时长由编排结果决定。成品检查模块在退出成功前核验时长、解码、音轨、字幕等实际业务要求。引擎不把文件存在等同于全部业务质量合格。

不需要修改 Meta-Skill、Creator-Skill 或 AI/ASR 接口。各提供方解释/合成逻辑保持独立，本引擎只负责确定性的执行与可观察性。
