"""Run real V3.0-05 planning, or explicitly labelled reference-audio engineering validation."""
import argparse
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from creator_dna.agents import CodexAdapter
from creator_dna.production import ProductionWorkflow, default_config
from creator_dna.production_assets import import_asset
from creator_dna.storage import Workspace, digest_file, read_json, write_json
from creator_dna.subtitles import load_timing, sentence_cues, speech_input
from creator_dna.video_style import build_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--creator", required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--speech", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--timing", type=Path)
    parser.add_argument("--reference-validation", action="store_true", help="仅参考原声音工程链路，不能当作新 TTS 验收")
    parser.add_argument("--source-id")
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--director", choices=["local", "codex"], default="local")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--model")
    args = parser.parse_args()
    if args.reference_validation:
        if args.speech or not args.source_id or not args.transcript:
            parser.error("参考验证须指定 source-id/transcript，不能同时指定 speech。")
    elif not args.speech or not args.registry:
        parser.error("新口播验收须指定真实 speech.json 和授权 registry。")
    workspace = Workspace(args.workspace)
    library = read_json(args.library)
    # Enforce source-directory separation even for the validation helper.
    source_root = Path(library["input_folder"]).resolve()
    if source_root == workspace.root or source_root in workspace.root.parents or workspace.root in source_root.parents:
        parser.error("workspace 与源素材目录须互不包含。")
    folder = workspace.root / ("production-validation-"+uuid.uuid4().hex[:10])
    folder.mkdir(parents=True)
    profile = build_profile(args.library)
    profile_path = folder / "video-style-profile.json"
    write_json(profile_path, profile)
    before = {s["path"]: (digest_file(s["path"]), Path(s["path"]).stat().st_mtime_ns)
              for s in library["sources"] if s["status"] == "analyzed"}
    kwargs = {}
    if args.reference_validation:
        source = next(s for s in library["sources"] if s["source_id"] == args.source_id and s["status"] == "analyzed")
        transcript = read_json(args.transcript)
        if transcript["source_id"] != source["source_id"] or transcript["source_sha256"] != source["sha256"]:
            parser.error("转录不是当前视频的证据。")
        # First five *actual ASR intervals*, no proportional timing or guessed speech.
        segments = transcript["segments"][:5]
        start, end = segments[0]["start"], segments[-1]["end"]
        video, audio = folder/"reference-excerpt.mp4", folder/"reference-audio.wav"
        def ffmpeg(items):
            subprocess.run(["ffmpeg","-v","error","-nostdin","-n",*items],check=True,capture_output=True)
        ffmpeg(["-ss",str(start),"-i",source["path"],"-t",str(end-start),"-an","-c:v","libx264","-pix_fmt","yuv420p",str(video)])
        ffmpeg(["-ss",str(start),"-i",source["path"],"-t",str(end-start),"-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(audio)])
        timing = folder/"reference-asr-timing.json"
        write_json(timing,{"schema_version":"3.0-05","audio_sha256":digest_file(audio),
                    "text":"".join(s["text"] for s in segments),"method":"asr",
                    "provenance":"已有本地 faster-whisper 时间戳，未经过新 TTS 或人工逐词同步验收；"+str(args.transcript.resolve()),
                    "cues":[{"cue_id":f"asr-{i:04d}","start":s["start"]-start,"end":s["end"]-start,
                             "text":s["text"],"timing_basis":"reference_asr_sentence_not_tts_acceptance"} for i,s in enumerate(segments)]})
        frame = next(f for f in source["frames"] if f["time"] >= start)
        screenshot = folder/"reference-screenshot.png"
        shutil.copyfile(args.library.resolve().parent/frame["path"],screenshot)
        note = "用户本次任务的本地工程验证范围；仅本地检查，不表示获得对外发布原视频的许可。"
        assets = [import_asset(video,"reference-portrait","portrait","video",source["relative_path"],"authorized",note,[],
                    "原声对应参考视频片段；不是新口型成品，仅验证主音频偏移。",audio_sha256=digest_file(audio)),
                  import_asset(screenshot,"reference-screenshot","screenshot","image",source["relative_path"]+" 的采样截图",
                    "authorized",note,["AI","机器人"],"完整参考画面截图，包含标题与人物；不是独立机器人产品画面。")]
        registry = folder/"assets.json"
        write_json(registry,{"schema_version":"3.0-05","assets":assets})
        kwargs = {"audio":audio,"timing":timing}
        if args.director == "local":
            cues = sentence_cues(read_json(timing)["cues"],default_config(profile["output"])["subtitle_style"])
            direction = {"schema_version":"3.0-05","scenes":[]}
            for i,c in enumerate(cues):
                direction["scenes"].append({"cue_ids":[c["cue_id"]],"role":"portrait" if i==0 else "b_roll",
                    "topics":[] if i==0 else ["机器人"],"keywords":[k for k in ("AI","机器人") if k in c["text"]],
                    "preferred_asset_id":"reference-portrait" if i==0 else "reference-screenshot",
                    "asset_policy":"selected",
                    "reason":"显式参考音频工程验证，人物口播为主、参考画面截图穿插；不代表生产或风格验收。",
                    "layout":"center","background_asset_id":None,"transition":{"type":"cut","duration":0}})
            direction_path = folder/"reference-direction.json"
            write_json(direction_path,direction)
            kwargs["direction_path"] = direction_path
    else:
        registry = args.registry
        kwargs = {"speech":args.speech,"timing":args.timing}
    agent = CodexAdapter(args.codex_bin,args.model,300) if args.director == "codex" else None
    result = ProductionWorkflow(workspace,agent).plan(args.creator,profile_path,registry,
                                                    local=args.director=="local",**kwargs)
    after = {path:(digest_file(path),Path(path).stat().st_mtime_ns) for path in before}
    summary = {"reference_audio_only":args.reference_validation,
               "new_generated_audio_input_verified":not args.reference_validation,
               "new_generated_audio_acceptance":"not_run" if args.reference_validation else "technical_passed_human_review_pending" if result["ready_for_render"] else "needs_assets",
               "source_files_unchanged":before==after,"profile":str(profile_path),"result":result,
               "timing_note":"ASR 初始时间戳需人工听验；参考原声不能代替新生成口播验收。" if args.reference_validation else "新生成 PCM 时间基准。"}
    if before != after:
        raise AssertionError("源素材被修改")
    write_json(folder/"validation-summary.json",summary)
    print(json.dumps({"summary":str(folder/"validation-summary.json"),**summary},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
