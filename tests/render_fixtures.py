"""Synthetic engineering media ONLY. No cloud TTS or face/lip inference."""
import math
import struct
import subprocess
import wave
from pathlib import Path

from PIL import Image

from creator_dna.production import default_config
from creator_dna.production_assets import import_asset
from creator_dna.storage import digest_file, write_json
from creator_dna.video_assets import VideoAssetWorkflow
from creator_dna.video import LocalVideoAnalyzer
from creator_dna.video_style import build_profile


def fixture(root, workspace, duration=4, portrait=False):
    root=Path(root)
    source=root/"synthetic-source"
    source.mkdir(exist_ok=True)
    video=source/"portrait-test-pattern.mp4"
    size="180x320" if portrait else "320x180"
    subprocess.run(["ffmpeg","-v","error","-nostdin","-n","-f","lavfi","-i",f"testsrc2=s={size}:r=25:d={duration}",
                    "-c:v","libx264","-threads","2","-pix_fmt","yuv420p",str(video)],check=True,capture_output=True)
    audio=source/"tone.wav"
    with wave.open(str(audio),"wb") as out:
        out.setparams((1,2,16000,0,"NONE","not compressed"))
        out.writeframes(b"".join(struct.pack("<h",round(2500*math.sin(i*2*math.pi*440/16000))) for i in range(round(duration*16000))))
    image=source/"blue.png"
    Image.new("RGB",(80,120),"blue").save(image)
    bg=source/"green.png"
    Image.new("RGB",(150,90),"green").save(bg)
    library=VideoAssetWorkflow(workspace,LocalVideoAnalyzer(max_frames=2,face_detection=False)).analyze(source,"synthetic-render")
    profile=build_profile(library["library"])
    profile_path=root/"profile.json"
    write_json(profile_path,profile)
    assets=[import_asset(video,"portrait","portrait","video","SYNTHETIC","owned","synthetic only",[],"TEST PATTERN, NOT PERSON",audio_sha256=digest_file(audio)),
            import_asset(image,"broll","screenshot","image","SYNTHETIC","owned","synthetic only",["AI"],"blue test B-roll"),
            import_asset(bg,"bg","background","image","SYNTHETIC","owned","synthetic only",[],"green test background")]
    registry=root/"assets.json"
    assets[0]["portrait_provenance"]={"source_id":"SYNTHETIC-NOT-A-PERSON","source_path":str(video.resolve()),
        "source_sha256":digest_file(video),"framing":"source_frame",
        "segments":[{"start":0,"end":duration,"source_start":0,"source_end":duration}]}
    write_json(registry,{"schema_version":"3.0-05","assets":assets})
    timing=root/"timing.json"
    text="合成测试。AI画面。结尾。"
    cues=[{"cue_id":f"cue-{i:04d}","start":start,"end":end,"text":t,"timing_basis":"SYNTHETIC TEST TIMING ONLY"}
          for i,(start,end,t) in enumerate([(0,duration*.3,"合成测试。"),(duration*.35,duration*.6,"AI画面。"),(duration*.7,duration-.1,"结尾。")])]
    write_json(timing,{"schema_version":"3.0-05","audio_sha256":digest_file(audio),"text":text,
                       "method":"forced_alignment","provenance":"SYNTHETIC FIXTURE ONLY","cues":cues})
    config=default_config(profile["output"])
    config["layout"]["rect"]={"x":.1,"y":.1,"width":.8,"height":.8}
    config["background_asset_id"]="bg"
    pc=root/"production.json"
    write_json(pc,config)
    speech=source/"speech.json"
    write_json(speech,{"schema_version":"3.0-03","provider":"minimax","model":"SYNTHETIC-NOT-TTS","voice_asset":"synthetic",
        "created_at":"synthetic","text":text,"audio":audio.name,"sha256":digest_file(audio),"canonical_audio":audio.name,
        "canonical_duration_seconds":duration,"timing_note":"SYNTHETIC SINE WAVE, NOT REAL SPEECH",
        "metadata":{"duration_seconds":duration,"sample_rate":16000,"channels":1,"codec":"pcm_s16le","format":"wav","bytes":audio.stat().st_size},
        "segments":[{"index":0,"text":text,"text_start":0,"text_end":len(text),"audio":audio.name,"start_seconds":0,
                     "end_seconds":duration,"duration_seconds":duration,"timing_basis":"measured_audio_segment_boundaries",
                     "word_timestamps":None,"provider_timestamps":None}]})
    workflow=root/"video-config.json"
    write_json(workflow,{"schema_version":"3.0-06","profile":str(profile_path),"assets":str(registry),"speech":str(speech),
        "timing":str(timing),"production_config":str(pc),"director":"local","render":{"segment_seconds":1,"preset":"ultrafast"}})
    return {"video":video,"audio":audio,"profile":profile_path,"assets":registry,"timing":timing,"production":pc,
            "workflow":workflow,"text":text,"speech":speech}
