"""Synthetic path/routing tests; no evidence of real portrait quality."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creator_dna.errors import CreatorDNAError
from creator_dna.portrait import rank_candidates
from creator_dna.portrait_sources import raw_folder, creator_raw_folder, check_reused_portraits
from creator_dna.storage import Workspace, write_json
from creator_dna.video_workflow import script_stage
from test_lip_sync import minimal_library
from test_video_workflow import context


class RawPortraitRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.materials=self.root/"materials"/"synthetic"
        self.raw=self.materials/"raw_portraits"
        self.raw.mkdir(parents=True)
        self.edited=self.materials/"edited.mp4"
        self.edited.write_bytes(b"SYNTHETIC PATH FIXTURE, NOT MEDIA")
        self.library=minimal_library()
        self.library["input_folder"]=str(self.materials)
        self.library["sources"][0]["path"]=str(self.edited)

    def test_empty_or_missing_raw_folder_never_falls_back_to_edited_video(self):
        with self.assertRaisesRegex(CreatorDNAError,"不会回退"):
            rank_candidates(self.library)
        self.raw.rmdir()
        self.assertEqual(raw_folder(self.materials),self.raw)
        with self.assertRaisesRegex(CreatorDNAError,"未经剪辑、无字幕"):
            rank_candidates(self.library)

    def test_mixed_library_only_ranks_files_physically_inside_raw_folder(self):
        original=self.raw/"original.MOV"
        original.write_bytes(b"SYNTHETIC PATH FIXTURE, NOT MEDIA")
        source=copy.deepcopy(self.library["sources"][0])
        source.update(path=str(original),source_id="raw-source")
        for c in source["candidates"]:
            c["candidate_id"]="raw-"+c["candidate_id"]
        self.library["sources"].append(source)
        ranked=rank_candidates(self.library)
        self.assertEqual({c["source_path"] for c in ranked},{str(original)})
        self.assertEqual({c["source_id"] for c in ranked},{"raw-source"})

    def test_added_raw_file_requires_reindexing_old_edited_library(self):
        (self.raw/"new.mp4").write_bytes(b"SYNTHETIC PATH FIXTURE")
        with self.assertRaisesRegex(CreatorDNAError,"素材库未更新"):
            rank_candidates(self.library)

    def test_raw_folder_library_cannot_include_an_edited_sibling(self):
        self.library["input_folder"]=str(self.raw)
        with self.assertRaisesRegex(CreatorDNAError,"不会回退"):
            rank_candidates(self.library)

    def test_old_static_or_edited_portrait_provenance_is_rejected(self):
        original=self.raw/"original.mp4"
        original.write_bytes(b"SYNTHETIC PATH FIXTURE")
        asset={"kind":"portrait","portrait_provenance":{"source_path":str(original),"reference_type":"video"}}
        check_reused_portraits([asset],self.raw)
        for value in ({"source_path":str(self.edited),"reference_type":"video"},
                      {"source_path":str(original),"reference_type":"image"}):
            with self.assertRaisesRegex(CreatorDNAError,"不能复用"):
                check_reused_portraits([{"kind":"portrait","portrait_provenance":value}],self.raw)

    def test_preflight_blocks_queued_static_fallback_before_tts_credentials(self):
        ws=Workspace(self.root/"workspace")
        write_json(ws.creator("synthetic")/"manifest.json",{"input_folder":str(self.materials)})
        self.assertEqual(creator_raw_folder(ws,"synthetic"),self.raw)
        cfg={"lipsync":{},"tts":{},"portrait":{"image":str(self.edited)}}
        # Nonempty config objects represent a queued task; no provider is used.
        cfg["lipsync"]={"repository":"not-run"};cfg["tts"]={"provider":"not-run"}
        ctx=context(self.root,ws,"raw-preflight");ctx.creator_id="synthetic"
        with patch("creator_dna.video_workflow.make_provider") as provider:
            with self.assertRaisesRegex(CreatorDNAError,"不能改用静态截图"):
                script_stage(ctx,{"workspace":str(ws.root),"config":cfg,"script":"SYNTHETIC","topic":None})
            provider.assert_not_called()
