# V3.0-05 验证记录（2026-10-09）

状态：视觉风格合同、独立编排 Skill、素材选择/准备、ASS 字幕、场景/布局、时间轴校验/重建及 CLI 已实现。**用户确认尚未生成新口播音频，因此“至少一段真实生成口播音频”的最终验收未完成。**没有重新开发或调用 TTS/口型服务，也没有用原声、静音 fixture 冒充新生成音频。

## 回归与工程检查

激活仓库 `.venv` 后执行规定命令：

```powershell
& .\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

最终运行 **152 项，150 项通过，2 项跳过，无失败**，耗时 35.399 秒。日志：`workspace/v3-05/unittest-final.log`。跳过的是既有真实付费 TTS 和真实本地口型模型集成测试，不能计作质量通过。

新增 22 项测试覆盖：Schema/CLI；服务商毫秒时间、实际分段偏移；正文/音频哈希不一致、单位不明、越界和重叠；无时间戳的多句/长段落拒绝字数估时；细时间戳断句；中英文/标点/ASS 控制转义；跨换行关键词高亮；厘秒边界；未知视觉风格；人工区间覆盖及伪造证据；授权/主题/区间/源变化；AlignmentProvider 接口；主音频/人像哈希和 B-roll 后偏移；背景与横屏 cover 构图；素材缺失草稿和口播回退；明确 missing 禁止同主题错误替换；异常 Agent 结果保存；时间轴编辑错误、素材存在、布局/裁切/帧率/转场/字幕；配置重建与 FFmpeg ASS 解析；TTS PCM 与原始分段内容核验（同长 canonical 替换也被拒绝）。

`tests/test_production.py` 的媒体和 Agent 均为明确标注的合成工程 fixture。它们只验证接口和行为，不证明真实视觉风格、声音、口型或感知字幕同步。

`git diff --check` 通过。所有新增 Schema 通过 Draft202012Validator.check_schema；独立 Skill 通过 skill-creator quick_validate（Windows 使用 `-X utf8`）。wheel 构建成功，检查 director Skill、其独立合同/输入说明、timeline Schema 与 production 模块均已打包。wheel 位于 `workspace/v3-05/wheels/`，没有覆盖其他工作成果或提交 Git。

## 真实参考素材工程验证

复用 V3.0-01 真素材库：

`workspace/v3-04/creators/creator-001/runs/video-analysis-184e5d0c3be74773bb6cd557512e8478/library.json`

选定按时长推荐的 `template-002`，显示 720×1280、24 fps、14 个来源，总长 1,536.916668 秒；候选镜头 102 个，中位候选镜头长 10.5625335 秒。它们是像素切镜测量，不是已确认的语义剪辑风格。人物/B-roll 占比、字体与包装等未审核项目保留未知，不从单帧或人脸比例编造视觉规则。

验证使用 `s-42edbc76ba6e46ea` 对应真实参考视频的前五个已有 faster-whisper 区间（0–9.32 秒）。原始转录：

`workspace/creators/creator-001/transcripts/s-42edbc76ba6e46ea/9d838557ece237b7933bc0d2528f9a3f6b6f947d493d290c4296b7ae67ee579b.json`

仅在 workspace 提取参考视频/单声道 PCM16 原声，复用已生成的关键帧截图。权限说明限制为本次本地工程检查，不宣称截图是独立产品画面或已取得对外发布许可。完整素材库的 18 个源文件 SHA256 和 mtime 在前后相同。

复现命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/validate_v3_production.py --workspace workspace/v3-05 --creator creator-001 --library workspace/v3-04/creators/creator-001/runs/video-analysis-184e5d0c3be74773bb6cd557512e8478/library.json --reference-validation --source-id s-42edbc76ba6e46ea --transcript workspace/creators/creator-001/transcripts/s-42edbc76ba6e46ea/9d838557ece237b7933bc0d2528f9a3f6b6f947d493d290c4296b7ae67ee579b.json
```

最终输入、profile 和验证摘要：`workspace/v3-05/production-validation-ae8dce676b/`。

最终可检查工程输出：`workspace/v3-05/creators/creator-001/runs/video-production-987c5cc8fa1f4c979f440c2c87bd5a48/`。

- `timeline.json`：主音频 9.32 秒；前景 0–5.72 人物，5.72–9.32 完整参考截图；背景/字幕/音频轨、源范围、布局和素材哈希齐全。此截图安排是显式工程用例，不是模仿作者或机器人产品实拍。
- `subtitles.ass`：2 个由真实 ASR 时间单元组合的字幕，白字、黑描边、黄色关键词，15 字/行、最多 2 行、下部安全边距。没有按字数推算时间。
- `validation.json`：valid=true、ready_for_render=true、issues=[]；表示技术合同可供下阶段渲染器使用。
- `assets/` 与 `audio.wav`：可搬移的验证副本，原素材只读。
- `subtitle-preview.png`：FFmpeg/libass 在 7 秒处的真实图片预览，已查看位置、换行与关键词颜色，没有输出生产级 MP4。原素材已有烧录标题/字幕，预览保留它们；当前不实现字幕擦除。

摘要明确写入 reference_audio_only=true、new_generated_audio_input_verified=false、new_generated_audio_acceptance=not_run。ASR 原始断句与时间尚需人工听验；这不能证明新 TTS 同步达标。

## 真实 Codex 接入

沿用现有 CodexAdapter 和本机配置，实际执行了编排请求。第一次真实调用发现 structured outputs 要求 const/enum 也有显式 type，已修订 Schema 生成器；失败请求与日志仍保留。修订后真实返回了合法编排；工程观察进一步暴露“标签相同的截图可能被自动重新选入”的问题，于是增加明确 asset_policy=missing。最终真实调用验证该缺失语义被正确执行。

最终真实 Agent 运行：

`workspace/v3-05/creators/creator-001/runs/video-production-5e99c950b81544f494e9720ea2537574/`

Agent 将开头作为人物观点镜头，反转句规划为 B-roll；指出完整参考截图不是独立机器人画面，返回 missing。装配器保留 1 个非阻塞 B-roll 缺项并回退同步口播。最终时间轴 valid=true、ready_for_render=true，素材需求没有被宽泛主题匹配覆盖。该结果证明真实 Agent、输出合同和确定性装配接通，不证明完整视频风格质量。

## 尚未完成及后续验收输入

尚无真实 V3.0-03 新生成 speech.json，也无本轮对应的新口型成品。待用户已有真实生成音频及可靠句级/强制对齐时间戳后，使用 README 的 production import/plan 与 `validate_v3_production.py --speech --registry` 生成新验收运行。真实生成音频、字幕感知同步、原作者视觉相似度及生产画面质量都需要独立检查；当前不填猜测分数。

本阶段不内置新的强制对齐模型，只提供可靠时间戳导入和显式 AlignmentProvider 接口；无对齐数据的长段落会给出可操作错误。素材搜索、AI 图片、字幕 OCR、字体识别、复杂转场、最终 MP4 渲染和发布均不在本轮实现范围。
