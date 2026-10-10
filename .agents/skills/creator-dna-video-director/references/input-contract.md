# 输入与独立复用

输入包含 script（口播正文）、timed_cues（cue_id/start/end/text/timing_basis，秒）、video_style_profile（选定模板输出与有证据的观察）、assets（asset_id/kind/media_type/description/topics/rights/rights_note/source_start/source_end/audio_offset 等）和 output。

visual profile 中 unknown 不能作为风格规则。素材时长是实测源时长，可用范围以 source_start/source_end 为准，图片没有视频时长。portrait 为已有口型成品；audio_offset 是源素材首个可用帧对应主口播音频的位置，不能把 B-roll 期间跳过的人像音频压缩到之后的镜头。仅做规划，不实现或替代口型推理。

输出使用本目录 video-direction.json。scenes 保留全部可靠字幕 ID，字幕之间的短停顿由人物/场景持续显示覆盖；不要发明停顿字幕。keywords 必须来自该场景对应原文。asset_policy=selected 时明确选择已有素材；auto 时允许本地标签匹配；missing 明确拒绝当前素材并保留需求，交由素材报告记录缺失。不能让缺失判断在标签匹配时被忽略。

独立 Agent 可使用该合同输出，再由任意遵循此合同的时间轴装配器绑定媒体路径和音频时间。该 Skill 不依赖原素材、蒸馏 Meta-Skill 或本 Python 包。
