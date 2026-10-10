# V3.0-01 工程验证记录

验证日期：2026-10-09（Asia/Shanghai）。本次没有提供真实创作者视频，全部媒体为本地 FFmpeg 合成测试，不能代表真实视觉风格、复杂场景分类或口型同步质量。

## 完整流程

使用 `scripts/validate_v3_video.py` 生成新素材，随后使用实际 `analyze-video` CLI 对同一只读文件夹重跑验证。原输入：`materials/v3-synthetic`；产物根：`workspace/v3-validation`；最近一次运行：`video-analysis-f369734443c54aa8a37b49652aec444c`。

输入包含横屏红/蓝已知切镜、竖屏测试图、旋转+SAR 测试图和异常 MOV 文件，没有真实人像或真实声音（AAC 仅为合成正弦信号）。

| 验证项 | 结果 |
|---|---|
| 批量分析 | 4 个文件，3 成功、1 异常，状态 partial |
| 成功视频总时长 | 5.002 秒 |
| 已知镜头边界 | 横屏素材的 1.000 秒切镜准确定位 |
| 音频 | 横屏 H.264 / AAC，48000 Hz；其余成功素材无音轨 |
| 输出模板 | 320×180 @25；180×320 @30000/1001；180×480 @25 |
| 旋转和 SAR | 编码 240×180 / SAR 2:1 / 旋转 90°，实际显示 180×480 / DAR 3:8 |
| 可复用候选 | 4 个索引片段，全部 pending_review，category/confidence 为空 |
| 源文件保护 | 全部输入 SHA-256 和 mtime_ns 前后一致，验证数据保存在 validation.json |
| 样本去重 | 实际解码时间戳无重复，不把 seek 重复当作重复素材证据 |
| 资产文件 | library.json、report.md、review-template.json、probe/log/关键帧/联系图均可读取 |
| 外部服务 | 未上传视频、人像或声音，未调用 AI/ASR 服务 |

## 自动化与兼容性

在项目虚拟环境执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

68 项测试全部通过，无跳过：V1.0 原有 25 项、V2.0 原有 24 项、V3.0-01 新增 19 项。视频测试实际运行 FFmpeg/FFprobe，覆盖不同比例/帧率、切镜、真实采样时间戳、旋转+SAR、异常文件、来源变更拒绝入库、原文件保持不变、采样上限、联系图分页、非零起始 PTS、全失败不替换 latest、CLI 退出码和中断检查点。

环境：Python 3.14.6、FFmpeg/FFprobe 6.1.1、Pillow 12.3.0、OpenCV Headless 4.14.0。`doctor` 可发现依赖。0.3.0 wheel 构建成功，包含视频模块和 `schemas/video-library.json`；未改变原有 Skill 包资源结构。`git diff --check` 无空白错误。

已视觉检查竖屏和旋转/SAR 联系图，时间标签与索引对应，显示比例正常。分页面板由自动化测试验证生成文件与页数。

## 尚未验收的内容

真实创作者的主要构图、固定场景、口部遮挡、人物清晰度通过阈值、动作/表情、烧录字幕和 B-roll 语义均未进行真实素材验收。Haar 人脸检测不识别身份，灰度画面相似不证明场景或动作重复。没有实现 TTS、口型同步、审核工作表自动合并或视频生成。后续应先人工审核少量真实片段，在授权本地环境独立验证口部重定向、人物身份保持和时间一致性。
