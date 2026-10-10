"""Synthetic engineering tests, not evidence of real visual style, speech or lip sync."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock

from jsonschema import Draft202012Validator

from creator_dna.cli import main, parser
from creator_dna.contracts import schema
from creator_dna.errors import CreatorDNAError
from creator_dna.production import ProductionWorkflow, check_direction, default_config, local_direction
from creator_dna.production_assets import from_library, import_asset, select_asset
from creator_dna.storage import Workspace, digest_file, read_json, write_json
from creator_dna.subtitles import check_cues, load_timing, provider_cues, sentence_cues, speech_input, write_ass
from creator_dna.timeline import validate_config, validate_timeline
from creator_dna.video import LocalVideoAnalyzer
from creator_dna.video_assets import VideoAssetWorkflow
from creator_dna.video_style import build_profile, review_template


def cue(text="合成测试。", start=0, end=1, cid="cue-0000"):
    return {"cue_id": cid, "start": start, "end": end, "text": text, "timing_basis": "synthetic_test_only"}


class SubtitleContractTests(unittest.TestCase):
    def test_all_schemas_and_cli(self):
        for name in ("production-assets", "video-style-profile", "video-style-review", "production-config",
                     "subtitle-timing", "video-direction", "video-timeline"):
            Draft202012Validator.check_schema(schema(name))
        args = parser().parse_args(["production", "plan", "--creator", "synthetic", "--profile", "p.json",
                                   "--registry", "a.json", "--speech", "speech.json"])
        self.assertEqual(args.director, "codex")

    def test_provider_measured_offsets_and_pause(self):
        manifest = {"segments": [{"text": "测试。", "start_seconds": 2, "end_seconds": 3,
                        "provider_timestamps_unit": "milliseconds", "timestamps_relative_to": "segment_audio",
                        "provider_timestamps": [{"text": "测试。", "time_begin": 100, "time_end": 900}]}]}
        c = provider_cues(manifest, 30)[0]
        self.assertAlmostEqual(c["start"], 2.1)
        self.assertAlmostEqual(c["end"], 2.9)
        for change in ("wrong_text", "out_of_range", "unknown_unit", "overlap"):
            bad = copy.deepcopy(manifest)
            s = bad["segments"][0]
            if change == "wrong_text":
                s["provider_timestamps"][0]["text"] = "编造。"
            elif change == "out_of_range":
                s["provider_timestamps"][0]["time_end"] = 2000
            elif change == "unknown_unit":
                s["provider_timestamps_unit"] = "seconds"
            else:
                s["provider_timestamps"] *= 2
            with self.subTest(change=change), self.assertRaises(CreatorDNAError):
                provider_cues(bad, 30)

    def test_coarse_single_sentence_only_never_char_rate(self):
        m = {"segments": [{"text": "一句。", "start_seconds": 4, "end_seconds": 5, "provider_timestamps": None}]}
        self.assertEqual(provider_cues(m, 30)[0]["start"], 4)
        m["segments"][0]["text"] = "第一句。第二句。"
        with self.assertRaisesRegex(CreatorDNAError, "强制对齐"):
            provider_cues(m, 30)
        m["segments"][0]["text"] = "一个超长句子"*20
        with self.assertRaises(CreatorDNAError):
            provider_cues(m, 30)

    def test_word_grouping_keeps_actual_boundaries(self):
        style = default_config()["subtitle_style"]
        words = [cue("Hello ", .2, .5, "w0"), cue("AI。", .6, .9, "w1"), cue("下一句。", 1.5, 2, "w2")]
        result = sentence_cues(words, style)
        self.assertEqual([(c["start"], c["end"], c["text"]) for c in result], [(.2,.9,"Hello AI。"),(1.5,2,"下一句。")])
        with self.assertRaises(CreatorDNAError):
            sentence_cues([cue("长"*31)], style)

    def test_timing_rejects_overlap_nan_and_wrong_text(self):
        for cues in ([cue(start=1,end=.5)], [cue(start=float("nan"))], [cue(),cue(cid="other",start=.5,end=2)]):
            with self.assertRaises(CreatorDNAError):
                check_cues(cues, "合成测试。", 3)
        with self.assertRaises(CreatorDNAError):
            check_cues([cue()], "不同文本", 3)

    def test_ass_chinese_english_line_break_highlight_and_injection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"test.ass"
            style = default_config()["subtitle_style"]
            style.update(max_chars_per_line=8, max_lines=3)
            c = {**cue("Hello AI。中文{\\pos}"), "keywords": ["AI"]}
            write_ass(path, [c], {"width":720,"height":1280}, style)
            content = path.read_text(encoding="utf-8")
            self.assertIn("\\N", content)
            self.assertIn("{\\c&H0000D4FF&}AI", content)
            self.assertNotIn("{\\pos}", content)
            self.assertIn("0:00:01.00", content)
            with self.assertRaises(CreatorDNAError):
                write_ass(path, [{**cue(start=0,end=.001),"keywords":[]}], {"width":720,"height":1280}, style)

    def test_highlight_survives_line_break_inside_keyword(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"split.ass"
            style=default_config()["subtitle_style"]
            style.update(max_chars_per_line=4,max_lines=3)
            write_ass(path,[{**cue("我们聊机器人。"),"keywords":["机器人"]}],{"width":720,"height":1280},style)
            text=path.read_text(encoding="utf-8")
            self.assertIn("{\\c&H0000D4FF&}机",text)
            self.assertIn("{\\c&H0000D4FF&}器人",text)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires local FFmpeg")
class ProductionMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "source"
        cls.source.mkdir()
        cls.video = cls.source / "synthetic.mp4"
        subprocess.run(["ffmpeg","-v","error","-nostdin","-n","-f","lavfi","-i",
                        "testsrc2=s=180x320:r=25:d=4","-c:v","libx264",str(cls.video)], check=True, capture_output=True)
        cls.workspace = Workspace(cls.root/"workspace")
        r = VideoAssetWorkflow(cls.workspace,LocalVideoAnalyzer(max_frames=3,face_detection=False)).analyze(cls.source,"synthetic")
        cls.library_path = Path(r["library"])
        cls.library = read_json(cls.library_path)
        cls.profile = build_profile(cls.library_path)
        cls.profile_path = cls.root/"profile.json"
        write_json(cls.profile_path, cls.profile)
        cls.audio = cls.source/"synthetic.wav"
        with wave.open(str(cls.audio),"wb") as a:
            a.setparams((1,2,16000,0,"NONE","not compressed"))
            a.writeframes(b"\0\0"*64000)
        from PIL import Image
        cls.image = cls.source/"synthetic.png"
        Image.new("RGB",(120,80),"blue").save(cls.image)
        cls.assets = [import_asset(cls.video,"portrait","portrait","video","synthetic fixture","owned",
                                   "synthetic test only",[],"synthetic test only", audio_sha256=digest_file(cls.audio)),
                      import_asset(cls.image,"screenshot","screenshot","image","synthetic fixture","owned",
                                   "synthetic test only",["AI"],"synthetic test only")]
        cls.registry = cls.root/"assets.json"
        write_json(cls.registry,{"schema_version":"3.0-05","assets":cls.assets})
        cls.timing = cls.root/"timing.json"
        cls.cues = [cue("合成测试。",0,1),cue("AI画面。",1.5,2.5,"cue-0001"),cue("结尾。",3,3.8,"cue-0002")]
        write_json(cls.timing,{"schema_version":"3.0-05","audio_sha256":digest_file(cls.audio),"text":"合成测试。AI画面。结尾。",
                              "method":"forced_alignment","provenance":"SYNTHETIC ENGINEERING FIXTURE ONLY","cues":cls.cues})
        cls.before = {p.name:(digest_file(p),p.stat().st_mtime_ns) for p in cls.source.iterdir()}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def plan(self, **kwargs):
        return ProductionWorkflow(self.workspace,kwargs.pop("agent",None)).plan("synthetic",self.profile_path,
                    kwargs.pop("registry",self.registry),audio=self.audio,timing=self.timing,local=True,**kwargs)

    def test_profile_unknowns_and_review_coverage(self):
        self.assertIsNone(self.profile["screen_time"]["b_roll_ratio"])
        self.assertEqual(self.profile["screen_time"]["unknown_ratio"],1)
        review = review_template()
        source = self.library["sources"][0]
        review["intervals"] = [{"source_id":source["source_id"],"start":0,"end":1,"role":"portrait","frame_ids":[]}]
        p = build_profile(self.library_path,review=review)
        self.assertAlmostEqual(p["screen_time"]["portrait_ratio"],.25)
        self.assertAlmostEqual(p["screen_time"]["unknown_ratio"],.75)
        review["intervals"] *= 2
        with self.assertRaises(CreatorDNAError):
            build_profile(self.library_path,review=review)

    def test_review_cannot_invent_frames_or_style(self):
        review = review_template()
        obs = review["observations"]["font"]
        obs.update(status="manual",value="invented font")
        with self.assertRaises(CreatorDNAError):
            build_profile(self.library_path,review=review)
        obs["evidence"] = [{"source_id":self.library["sources"][0]["source_id"],"start":0,"end":1,"frame_ids":["invented"]}]
        with self.assertRaises(CreatorDNAError):
            build_profile(self.library_path,review=review)

    def test_library_inventory_does_not_grant_rights(self):
        inventory = from_library(self.library)
        self.assertTrue(all(a["rights"] == "unknown" for a in inventory["assets"]))
        self.assertIsNone(select_asset(inventory["assets"],"b_roll",["AI"],1)[0])

    def test_authorized_match_and_short_or_changed_asset_rejected(self):
        a,start,reason = select_asset(self.assets,"b_roll",["AI"],1)
        self.assertEqual(a["asset_id"],"screenshot")
        self.assertIn("AI",reason)
        self.assertIsNone(select_asset(self.assets,"b_roll",["other"],1)[0])
        self.assertIsNone(select_asset(self.assets,"portrait",[],5)[0])
        self.assertIsNone(select_asset(self.assets,"portrait",[],1,audio_sha256="0"*64)[0])
        bad = copy.deepcopy(self.assets)
        bad[1]["rights"]="denied"
        self.assertIsNone(select_asset(bad,"b_roll",["AI"],1)[0])
        bad[1]["rights"]="owned"
        bad[1]["sha256"]="0"*64
        self.assertIsNone(select_asset(bad,"b_roll",["AI"],1)[0])

    def test_audio_binding_and_aligner_interface(self):
        aligner = Mock()
        aligner.align.return_value = read_json(self.timing)
        cues = load_timing(self.audio,read_json(self.timing)["text"],4,aligner=aligner)
        self.assertEqual(len(cues),3)
        aligner.align.return_value["audio_sha256"]="0"*64
        with self.assertRaises(CreatorDNAError):
            load_timing(self.audio,read_json(self.timing)["text"],4,aligner=aligner)

    def test_complete_timeline_uses_audio_offsets_across_broll(self):
        r = self.plan()
        t = read_json(r["timeline"])
        self.assertTrue(r["ready_for_render"])
        self.assertEqual([(c["start"],c["end"],c["role"]) for c in t["foreground"]],[(0,1.5,"portrait"),(1.5,3,"b_roll"),(3,4,"portrait")])
        self.assertEqual(t["foreground"][2]["source_start"],3)
        self.assertEqual(t["output"]["orientation"],"portrait")
        self.assertEqual(self.before,{p.name:(digest_file(p),p.stat().st_mtime_ns) for p in self.source.iterdir()})
        self.assertTrue(all((Path(r["timeline"]).parent/a["path"]).is_file() for a in t["assets"]))

    def test_background_scene_and_explicit_landscape_layout(self):
        assets=copy.deepcopy(self.assets)
        bg=import_asset(self.image,"background","background","image","synthetic fixture","owned",
                        "synthetic test only",[],"synthetic background")
        assets.append(bg)
        registry=self.root/"background-assets.json"
        write_json(registry,{"schema_version":"3.0-05","assets":assets})
        config=default_config(self.profile["output"])
        config["output"]={"width":320,"height":180,"frame_rate":"30000/1001","orientation":"landscape","pixel_aspect_ratio":"1"}
        config["layout"].update(fit="cover",anchor="right",rect={"x":.1,"y":.1,"width":.8,"height":.8})
        config["background_asset_id"]="background"
        path=self.root/"background-config.json"
        write_json(path,config)
        r=self.plan(registry=registry,config_path=path)
        self.assertTrue(r["ready_for_render"])
        t=read_json(r["timeline"])
        self.assertEqual(t["output"]["frame_rate"],"30000/1001")
        self.assertEqual(len(t["background"]),3)
        self.assertEqual(t["foreground"][0]["layout"]["fit"],"cover")
        self.assertEqual(t["foreground"][0]["layout"]["anchor"],"right")

    def test_missing_assets_persist_draft_without_claiming_ready(self):
        empty=self.root/"empty.json"
        write_json(empty,{"schema_version":"3.0-05","assets":[]})
        r=self.plan(registry=empty)
        self.assertEqual(r["status"],"needs_assets")
        self.assertFalse(r["ready_for_render"])
        self.assertTrue(any(m["blocking"] for m in r["missing_assets"]))
        self.assertTrue(Path(r["subtitles"]).is_file())

    def test_missing_broll_falls_back_and_records_reason(self):
        direction = local_direction(self.cues,self.assets)
        direction["scenes"][1]["topics"]=["missing-topic"]
        direction_path=self.root/"missing-direction.json"
        write_json(direction_path,direction)
        r=self.plan(direction_path=direction_path)
        self.assertTrue(r["ready_for_render"])
        self.assertEqual(len(r["missing_assets"]),1)
        self.assertFalse(r["missing_assets"][0]["blocking"])

    def test_director_missing_choice_overrides_same_topic_asset(self):
        direction = local_direction(self.cues,self.assets)
        direction["scenes"][1]["asset_policy"]="missing"
        path=self.root/"semantic-missing.json"
        write_json(path,direction)
        r=self.plan(direction_path=path)
        self.assertTrue(r["ready_for_render"])
        t=read_json(r["timeline"])
        self.assertEqual(t["foreground"][1]["role"],"portrait")
        self.assertEqual(t["missing_assets"][0]["role"],"b_roll")

    def test_validator_rejects_edit_errors_missing_and_source_range(self):
        r=self.plan()
        original=read_json(r["timeline"])
        for kind in ("gap","overlap","time","crop","stretch","fps","missing","rights","source","audio_offset","subtitle","nan","orientation","transition"):
            t=copy.deepcopy(original)
            c=t["foreground"][0]
            if kind=="gap": c["start"]=.2
            elif kind=="overlap": t["foreground"][1]["start"]=1
            elif kind=="time": c["end"]=5
            elif kind=="crop": c["layout"]["crop"]={"x":.8,"y":0,"width":.5,"height":1}
            elif kind=="stretch": c["layout"]["fit"]="stretch"
            elif kind=="fps": t["output"]["frame_rate"]="0/0"
            elif kind=="missing": t["assets"][0]["path"]="missing.mp4"
            elif kind=="rights": t["assets"][0]["rights"]="unknown"
            elif kind=="source": c["source_end"]=10
            elif kind=="audio_offset": t["assets"][0]["audio_offset"]=1
            elif kind=="subtitle": t["subtitles"]["cues"][0]["text"]="invented"
            elif kind=="nan": c["start"]=float("nan")
            elif kind=="orientation": t["output"]["orientation"]="landscape"
            else: c["transition"]={"type":"fade","duration":20}
            with self.subTest(kind=kind):
                report=validate_timeline(t,Path(r["timeline"]).parent)
                self.assertFalse(report["valid"],report)

    def test_agent_invalid_output_saved_and_not_published(self):
        agent=Mock()
        bad=local_direction(self.cues,self.assets)
        bad["scenes"][0]["cue_ids"]=["invented"]
        agent.run.return_value=bad
        with self.assertRaises(CreatorDNAError):
            ProductionWorkflow(self.workspace,agent).plan("synthetic",self.profile_path,self.registry,
                audio=self.audio,timing=self.timing)
        request=agent.run.call_args.args[0]
        self.assertEqual(request.schema_name,"video-direction")
        self.assertEqual(read_json(request.directory.parent/"run.json")["status"],"failed")
        self.assertTrue((request.directory.parent/"direction.json").is_file())
        self.assertFalse((request.directory.parent/"timeline.json").exists())

    def test_direction_rejects_unknown_asset_and_keywords(self):
        for kind in ("asset","keyword","duplicate"):
            direction=local_direction(self.cues,self.assets)
            if kind=="asset": direction["scenes"][0]["preferred_asset_id"]="invented"
            elif kind=="keyword": direction["scenes"][0]["keywords"]=["invented"]
            else: direction["scenes"][0]["cue_ids"].append(self.cues[1]["cue_id"])
            with self.assertRaises(CreatorDNAError):
                check_direction(direction,self.cues,self.assets)

    def test_edit_rebuild_and_ffmpeg_parses_ass(self):
        r=self.plan()
        t=read_json(r["timeline"])
        t["subtitles"]["style"]["color"]="#00FFFF"
        t["foreground"][0]["layout"]["anchor"]="left"
        self.assertFalse(validate_timeline(t,Path(r["timeline"]).parent)["valid"])
        edited=Path(r["timeline"]).parent/"edited.json"
        write_json(edited,t)
        rebuilt=ProductionWorkflow(self.workspace).rebuild("synthetic",edited)
        self.assertTrue(rebuilt["ready_for_render"])
        folder=Path(rebuilt["timeline"]).parent
        out=subprocess.run(["ffmpeg","-v","error","-nostdin","-f","lavfi","-i","color=black:s=180x320:d=4",
             "-vf","ass=subtitles.ass","-frames:v","1","-f","null","-"],cwd=folder,capture_output=True,text=True)
        self.assertEqual(out.returncode,0,out.stderr)

    def test_tts_manifest_real_pcm_and_path_integrity(self):
        manifest = {"schema_version":"3.0-03","provider":"minimax","model":"synthetic","voice_asset":"synthetic",
                    "created_at":"synthetic","text":"合成测试。","audio":"synthetic.wav","sha256":digest_file(self.audio),
                    "canonical_audio":"synthetic.wav","canonical_duration_seconds":4,"timing_note":"synthetic only",
                    "metadata":{"duration_seconds":4,"sample_rate":16000,"channels":1,"codec":"pcm_s16le","format":"wav","bytes":128044},
                    "segments":[{"index":0,"text":"合成测试。","text_start":0,"text_end":5,"audio":"synthetic.wav",
                                 "start_seconds":0,"end_seconds":4,"duration_seconds":4,"timing_basis":"measured_audio_segment_boundaries",
                                 "word_timestamps":None,"provider_timestamps":None}]}
        path=self.source/"synthetic-speech.json"
        try:
            write_json(path,manifest)
            self.assertEqual(speech_input(path)[1],4)
            different=self.source/"different-canonical.wav"
            with wave.open(str(different),"wb") as a:
                a.setparams((1,2,16000,0,"NONE","not compressed"))
                a.writeframes(b"\1\0"*64000)
            manifest["canonical_audio"]=different.name
            write_json(path,manifest)
            with self.assertRaisesRegex(CreatorDNAError,"PCM 内容"):
                speech_input(path)
            different.unlink()
            manifest["canonical_audio"]="../outside.wav"
            write_json(path,manifest)
            with self.assertRaises(CreatorDNAError):
                speech_input(path)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
