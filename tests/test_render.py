"""Real FFmpeg engineering checks; synthetic media cannot prove style quality."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from creator_dna.cli import parser
from creator_dna.errors import CreatorDNAError
from creator_dna.production import ProductionWorkflow
from creator_dna.render import frame_segments, settings
from creator_dna.storage import Workspace, digest_file, native_path, read_json, write_json
from creator_dna.tasks import TaskManager
from creator_dna.video_workflow import create_video_task
from creator_dna.video_cli import clean
from render_fixtures import fixture


class RenderContracts(unittest.TestCase):
    def test_cli_and_settings(self):
        self.assertEqual(parser().parse_args(["video","create","--creator","x","--config","x.json","--topic","新选题"]).operation,"create")
        for cfg in ({"segment_seconds":float("nan")},{"threads":False},{"preset":"typo"},{"crf":99},{"unknown":1}):
            with self.assertRaises(CreatorDNAError): settings(cfg)

    def test_fractional_frame_boundaries_no_gap_or_duplicate(self):
        t={"duration":4.03,"output":{"frame_rate":"30000/1001"},
           "foreground":[{"start":0,"end":1.501},{"start":1.501,"end":4.03}],"background":[]}
        s=frame_segments(t,.7)
        self.assertEqual(s[0]["first"],0)
        self.assertEqual(s[-1]["last"],121)
        self.assertTrue(all(a["last"]==b["first"] for a,b in zip(s,s[1:])))
        self.assertEqual(sum(x["last"]-x["first"] for x in s),121)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"requires FFmpeg")
class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="render Chinese 空格 ")
        cls.root=Path(cls.temp.name)
        cls.ws=Workspace(cls.root/"workspace")
        cls.f=fixture(cls.root,cls.ws)
        cls.plan=ProductionWorkflow(cls.ws).plan("synthetic-render",cls.f["profile"],cls.f["assets"],
            audio=cls.f["audio"],timing=cls.f["timing"],config_path=cls.f["production"],local=True)
        timeline=read_json(cls.plan["timeline"])
        for c in timeline["background"]:
            c["layout"].update(fit="cover",rect={"x":0,"y":0,"width":1,"height":1})
        write_json(cls.plan["timeline"],timeline)
        cls.before={p.name:digest_file(p) for p in cls.f["video"].parent.iterdir()}

    @classmethod
    def tearDownClass(cls):
        path=native_path(cls.root)
        assert cls.root.name.startswith("render Chinese ")
        shutil.rmtree(path)
        cls.temp.cleanup()

    def run_render(self,timeline=None,render=None):
        task=create_video_task(self.ws,"synthetic-render",timeline=timeline or self.plan["timeline"],render=render or {"segment_seconds":1,"preset":"ultrafast"})
        states=[]
        result=TaskManager(self.ws).run(task["task_id"],observer=lambda s:states.append(copy.deepcopy(s)))
        self.assertEqual(result["status"],"completed",result.get("error"))
        final=Path(next(s for s in result["stages"] if s["id"]=="final_check")["result"]["video"])
        return result,final,states

    def test_render_background_broll_subtitle_audio_and_cache(self):
        result,final,states=self.run_render()
        self.assertTrue(final.is_file())
        check=read_json(final.parent/"verification.json")
        self.assertEqual(check["automatic_checks"],"passed")
        self.assertEqual(check["human_review"],"pending")
        logs=TaskManager(self.ws).logs(result["task_id"],tail=10000)
        self.assertTrue(any(e["data"].get("progress",{}).get("unit")=="media_seconds" for e in logs))
        self.assertTrue(all(s["overall_progress"]["percent"] is None for s in states if s["status"]=="running"))
        r2,final2,_=self.run_render()
        records=read_json(next(p for p in r2["output_files"] if Path(p).name=="segments.json"))
        self.assertTrue(all(r["reused"] for r in records))
        self.assertNotEqual(final,final2)
        self.assertEqual(self.before,{p.name:digest_file(p) for p in self.f["video"].parent.iterdir()})
        # B-roll center is blue, while the exposed border is green.
        frame=subprocess.run(["ffmpeg","-v","error","-ss","1.7","-i",str(final),"-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","-"],capture_output=True,check=True).stdout
        center=frame[(50*320+160)*3:(50*320+160)*3+3]
        corner=frame[:3]
        self.assertGreater(center[2],200)
        self.assertGreater(corner[1],80)

    def test_full_workflow_existing_script_and_speech(self):
        task=create_video_task(self.ws,"synthetic-render",self.f["workflow"],script=self.f["text"])
        result=TaskManager(self.ws).run(task["task_id"])
        self.assertEqual(result["status"],"completed",result.get("error"))
        self.assertTrue(next(s for s in result["stages"] if s["id"]=="tts")["result"]["reused"])
        self.assertEqual(next(s for s in result["stages"] if s["id"]=="final_check")["result"]["human_review"],"pending")

    def test_edit_subtitle_reuses_unaffected_segments_and_never_tts(self):
        self.run_render()
        t=read_json(self.plan["timeline"])
        t["subtitles"]["cues"][0]["keywords"]=["合成"]
        path=Path(self.plan["timeline"]).parent/"edited-highlight.json"
        write_json(path,t)
        result,final,_=self.run_render(path)
        records=read_json(next(p for p in result["output_files"] if Path(p).name=="segments.json"))
        self.assertTrue(any(r["reused"] for r in records))
        self.assertTrue(any(not r["reused"] for r in records))
        self.assertNotIn("tts",[s["id"] for s in result["stages"]])

    def test_queued_mutation_fails_and_no_final(self):
        t=read_json(self.plan["timeline"])
        path=Path(self.plan["timeline"]).parent/"mutating.json"
        write_json(path,t)
        task=create_video_task(self.ws,"synthetic-render",timeline=path)
        t["foreground"][0]["layout"]["fit"]="cover"
        write_json(path,t)
        result=TaskManager(self.ws).run(task["task_id"])
        self.assertEqual(result["status"],"failed")
        self.assertFalse(any(Path(p).name=="final.mp4" for p in result["output_files"]))

    def test_noninteger_fps_fade_cover_anchor(self):
        t=read_json(self.plan["timeline"])
        t["output"]["frame_rate"]="30000/1001"
        t["foreground"][0]["transition"]={"type":"fade","duration":.4}
        t["foreground"][0]["layout"].update(fit="cover",anchor="right",crop={"x":.1,"y":0,"width":.8,"height":1})
        path=Path(self.plan["timeline"]).parent/"fractional.json"
        write_json(path,t)
        self.run_render(path,{"segment_seconds":.5,"preset":"ultrafast"})

    def test_audio_mix_and_cache_ignores_music_change(self):
        self.run_render()
        cfg={"segment_seconds":1,"preset":"ultrafast","audio_mix":[{"path":str(self.f["audio"]),"sha256":digest_file(self.f["audio"]),
             "start":.5,"end":3,"source_start":0,"volume":.1,"rights":"owned","rights_note":"synthetic tone only"}]}
        result,_,_=self.run_render(render=cfg)
        records=read_json(next(p for p in result["output_files"] if Path(p).name=="segments.json"))
        self.assertTrue(all(r["reused"] for r in records))

    def test_clean_preserves_deliverables_and_resume_hashes(self):
        result,final,_=self.run_render()
        sha=digest_file(final)
        manager=TaskManager(self.ws)
        cleaned=clean(manager,result["task_id"],cache=True)
        self.assertTrue(cleaned["checkpoints_preserved"])
        self.assertEqual(digest_file(final),sha)
        self.assertEqual(manager.run(result["task_id"])["status"],"completed")

    def test_black_output_fails_without_publishing_final(self):
        t=read_json(self.plan["timeline"])
        base=Path(self.plan["timeline"]).parent
        black=base/"black.mp4"
        subprocess.run(["ffmpeg","-v","error","-n","-f","lavfi","-i","color=black:s=320x180:r=25:d=4","-c:v","libx264",str(black)],capture_output=True,check=True)
        a=next(a for a in t["assets"] if a["kind"]=="portrait")
        a.update(path="black.mp4",sha256=digest_file(black))
        for c in t["foreground"]:
            c.update(asset_id=a["asset_id"],role="portrait",source_start=c["start"],source_end=c["end"])
            c["layout"]["rect"]={"x":0,"y":0,"width":1,"height":1}
        path=base/"black-timeline.json"
        write_json(path,t)
        task=create_video_task(self.ws,"synthetic-render",timeline=path,render={"preset":"ultrafast"})
        result=TaskManager(self.ws).run(task["task_id"])
        self.assertEqual(result["status"],"failed")
        self.assertEqual(result["error"]["type"],"FinalIntegrityError")
        self.assertFalse(any(Path(p).name=="final.mp4" for p in result["output_files"]))


if __name__=="__main__": unittest.main()
