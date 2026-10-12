"""Synthetic adapter tests, including real FFmpeg. Not real voice/face proof."""
import shutil
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creator_dna.lip_sync import LipSyncResult
from creator_dna.lip_sync_workflow import generate_stage
from creator_dna.storage import Workspace, digest_file, native_path, read_json, write_json
from creator_dna.task_executors import run_ffmpeg
from creator_dna.task_worker import StageContext, StageFailure
from creator_dna.video_workflow import lip_stage, script_stage
from render_fixtures import fixture
from test_workflow import FixtureAgent
from creator_dna.workflow import Workflow
from creator_dna.media import MediaProcessor


def context(root, ws, stage_id, attempt=1, inputs=None):
    directory=native_path(Path(root)/stage_id/f"attempt-{attempt:03d}")
    directory.mkdir(parents=True)
    return StageContext({"task_id":"task-"+"a"*32,"creator_id":"synthetic-render","stage_id":stage_id,
        "attempt":attempt,"inputs":inputs or {},"previous_attempts":[],"timeout":600,"materials":[],
        "cancel_file":str(ws.root/"cancel.json")},directory)


class SyntheticPortraitProvider:
    name="synthetic-test-pattern-NOT-A-MODEL"
    def __init__(self): self.calls=0; self.fail_on=2; self.floor_endpoint=False
    def environment(self): return {"ready":True,"synthetic_only":True}
    def fingerprint(self): return {"synthetic_only":True}
    def generate(self, request, ctx):
        self.calls+=1
        if self.calls==self.fail_on:
            raise StageFailure("SYNTHETIC SECOND SEGMENT FAILURE","SyntheticFailure")
        path=ctx.output_dir/"synthetic-pattern.mp4"
        duration=math.floor(request.duration*25)/25 if self.floor_endpoint else request.duration
        run_ffmpeg(ctx,["ffmpeg","-v","error","-n","-f","lavfi","-i",
            f"testsrc2=s={request.width}x{request.height}:r=25:d={duration}",
            "-c:v","libx264","-threads","2",str(path)],request.duration)
        return LipSyncResult(path,{"synthetic_only":True,"source_looped":False,"dropped_frames":0})


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"requires FFmpeg")
class VideoStageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="v3-stage-")
        self.root=Path(self.temp.name)
        self.ws=Workspace(self.root/"ws")
        self.f=fixture(self.root,self.ws,duration=15)
    def tearDown(self):
        path=native_path(self.root)
        assert self.root.name.startswith("v3-stage-")
        shutil.rmtree(path)
        self.temp.cleanup()

    def test_segmented_lip_adapter_resume_reuses_first_segment(self):
        cfg={"speech":str(self.f["speech"]),"lipsync":{"repository":str(self.root/"no-real-model")},
             "portrait":{"image":str(self.f["video"].parent/"blue.png")},
             "portrait_authorization":{"authorized":True,"source_sha256":[digest_file(self.f["video"].parent/"blue.png")],
                    "confirmation":"SYNTHETIC FIXTURE ONLY","confirmed_at":"synthetic"},
             "render":{"ffmpeg":"ffmpeg","ffprobe":"ffprobe"}}
        inputs={"tts":{"result":{"reused":True,"speech":str(self.f["speech"])},"artifacts":[]}}
        params={"workspace":str(self.ws.root),"config":cfg}
        provider=SyntheticPortraitProvider()
        def injected(ctx,p): return generate_stage(ctx,p,provider)
        ctx=context(self.root,self.ws,"lip_sync",inputs=inputs)
        with patch("creator_dna.video_workflow.generate_stage",side_effect=injected), patch("creator_dna.lip_sync.make_provider",return_value=provider):
            with self.assertRaises(StageFailure): lip_stage(ctx,params)
            self.assertTrue((ctx.attempt_dir.parent/"checkpoints/segment-0000/complete.json").is_file())
            provider.fail_on=-1
            second=context(self.root,self.ws,"lip_sync",2,inputs)
            result=lip_stage(second,params)
        self.assertEqual(provider.calls,3)  # First success + second fail + second retry, no first repeat.
        self.assertEqual(result["segments"],2)
        self.assertEqual(result["human_review"],"pending")
        a=read_json(second.output_dir/"portrait-asset.json")
        self.assertAlmostEqual(a["duration"],15,delta=.04)
        self.assertEqual(a["audio_sha256"],digest_file(self.f["audio"]))
        locked=read_json(second.attempt_dir.parent/"checkpoints/portrait-plan.json")
        self.assertEqual(a["portrait_provenance"]["source_sha256"],locked["source_lock"]["source_sha256"])
        # A forged cached receipt from another person must fail before reuse.
        receipt_path=second.attempt_dir.parent/"checkpoints/segment-0000/complete.json"
        receipt=read_json(receipt_path)
        record=next(r for r in receipt["artifacts"] if Path(r["path"]).name=="portrait.json")
        manifest=read_json(record["path"]);manifest["source_sha256"]="b"*64
        write_json(record["path"],manifest);record["sha256"]=digest_file(record["path"])
        write_json(receipt_path,receipt)
        third=context(self.root,self.ws,"lip_sync",3,inputs)
        with patch("creator_dna.lip_sync.make_provider",return_value=provider):
            with self.assertRaisesRegex(Exception,"整片锁定计划"):
                lip_stage(third,params)
        self.assertEqual(provider.calls,3)

    def test_fractional_backend_endpoint_keeps_audio_cut_positions(self):
        cfg={"speech":str(self.f["speech"]),"lipsync":{"repository":str(self.root/"no-real-model")},
             "portrait":{"image":str(self.f["video"].parent/"blue.png")},
             "portrait_authorization":{"authorized":True,"source_sha256":[digest_file(self.f["video"].parent/"blue.png")],
                    "confirmation":"SYNTHETIC FIXTURE ONLY","confirmed_at":"synthetic"},
             "render":{"ffmpeg":"ffmpeg","ffprobe":"ffprobe"}}
        inputs={"tts":{"result":{"reused":True,"speech":str(self.f["speech"])},"artifacts":[]}}
        provider=SyntheticPortraitProvider();provider.fail_on=-1;provider.floor_endpoint=True
        ctx=context(self.root,self.ws,"lip_sync",inputs=inputs)
        def injected(c,p): return generate_stage(c,p,provider)
        with patch("creator_dna.video_workflow.generate_stage",side_effect=injected),patch("creator_dna.lip_sync.make_provider",return_value=provider):
            lip_stage(ctx,{"workspace":str(self.ws.root),"config":cfg})
        audit=read_json(ctx.output_dir/"portrait-segments.json")
        self.assertTrue(all(x["cfr_endpoint_extension_frames"] in {0,1} for x in audit))
        self.assertAlmostEqual(read_json(ctx.output_dir/"portrait-asset.json")["duration"],15,delta=.001)

    def test_processor_success_without_output_never_publishes_portrait_asset(self):
        cfg={"speech":str(self.f["speech"]),"lipsync":{"repository":str(self.root/"no-real-model")},
             "portrait":{"image":str(self.f["video"].parent/"blue.png")},
             "portrait_authorization":{"authorized":True,"source_sha256":[digest_file(self.f["video"].parent/"blue.png")],
                    "confirmation":"SYNTHETIC FIXTURE ONLY","confirmed_at":"synthetic"},
             "portrait_processing_command":[sys.executable,"-c","pass","{request}"],
             "render":{"ffmpeg":"ffmpeg","ffprobe":"ffprobe"}}
        inputs={"tts":{"result":{"reused":True,"speech":str(self.f["speech"])},"artifacts":[]}}
        provider=SyntheticPortraitProvider();provider.fail_on=-1
        ctx=context(self.root,self.ws,"lip_sync",inputs=inputs)
        def injected(c,p): return generate_stage(c,p,provider)
        with patch("creator_dna.video_workflow.generate_stage",side_effect=injected),patch("creator_dna.lip_sync.make_provider",return_value=provider):
            with self.assertRaisesRegex(StageFailure,"人物处理失败"):
                lip_stage(ctx,{"workspace":str(self.ws.root),"config":cfg})
        self.assertFalse((ctx.output_dir/"portrait-asset.json").exists())

    def test_new_topic_extracts_body_only_and_pins_skill(self):
        source=self.root/"text-reference"
        source.mkdir()
        (source/"a.txt").write_text("你有没有先整理？先行动。",encoding="utf-8")
        (source/"b.txt").write_text("你有没有后选择？先分类。",encoding="utf-8")
        agent=FixtureAgent()
        workflow=Workflow(self.ws,MediaProcessor(),agent)
        r=workflow.distill(source,"synthetic-render")
        workflow.skills.approve("synthetic-render",r["version"])
        ctx=context(self.root,self.ws,"script")
        with patch("creator_dna.video_workflow.agent",return_value=agent):
            result=script_stage(ctx,{"workspace":str(self.ws.root),"config":{"skill_version":r["version"]},
                                    "topic":"全新工程选题","script":None})
        text=(ctx.output_dir/"script.txt").read_text(encoding="utf-8")
        self.assertEqual(text,"你有没有试过先整理文件，再选择工具？先从一个文件夹开始。")
        self.assertNotIn("Skill 版本",text)
        self.assertEqual(result["skill_version"],"v001")
