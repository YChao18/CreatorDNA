"""Persist a SYNTHETIC engineering MP4, or explicitly run three real model levels."""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

from creator_dna.storage import Workspace, now, read_json, write_json
from creator_dna.task_cli import TerminalProgress
from creator_dna.tasks import TaskManager
from creator_dna.video_workflow import create_video_task, load_config


RANGES={1:(10,20),2:(30,60),3:(120,180)}


def real_suite(workspace, suite_path):
    suite_path=Path(suite_path).resolve()
    cases=read_json(suite_path)["levels"]
    if [c["level"] for c in cases] != [1,2,3]:
        raise ValueError("真实验收要求按顺序提供 level 1、2、3。")
    results=[]
    for case in cases:
        cfg_path=(suite_path.parent/case["config"]).resolve()
        cfg=load_config(cfg_path)
        # Require the new-voice AND real portrait model path, no fixture/reused-audio substitute.
        if cfg.get("speech") or not cfg.get("tts") or not cfg.get("lipsync"):
            raise ValueError("真实全链路验证必须生成新 TTS 并执行真实口型模型，不接受已有 speech。")
        script=(suite_path.parent/case["script_file"]).read_text(encoding="utf-8-sig") if case.get("script_file") else None
        started=time.monotonic()
        task=create_video_task(workspace,case["creator"],cfg_path,topic=case.get("topic"),script=script)
        result=TaskManager(workspace).run(task["task_id"],observer=TerminalProgress())
        row={"level":case["level"],"task_id":task["task_id"],"task_status":result["status"],
             "elapsed_seconds":time.monotonic()-started,"human_review":"pending","error":result.get("error")}
        if result["status"] == "completed":
            final=next(s for s in result["stages"] if s["id"]=="final_check")["result"]["video"]
            verification=read_json(Path(final).parent/"verification.json")
            duration=verification["duration"]
            lo,hi=RANGES[case["level"]]
            row.update(video=final,duration=duration,automatic_checks=verification["automatic_checks"],
                       duration_range_passed=lo-.1<=duration<=hi+.1,bytes=Path(final).stat().st_size,
                       cost="未查询账单；查看 tts/checkpoints 请求凭据，不能据请求次数声称金额")
        results.append(row)
        write_json(workspace.root/"real-integration.json",{"kind":"real_models","created_at":now(),"levels":results,
                   "human_review":"pending","style_quality":"not_evaluated"})
        if result["status"] != "completed" or not row.get("duration_range_passed"):
            break  # Never attempt a longer level after an earlier technical failure.
    return {"kind":"real_models","levels":results,"human_review":"pending",
            "passed":len(results)==3 and all(r.get("duration_range_passed") and r.get("automatic_checks")=="passed" for r in results)}


def engineering(workspace_root, duration):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tests"))
    from render_fixtures import fixture
    root=Path(workspace_root).resolve()/f"engineering-{uuid.uuid4().hex[:12]}"
    root.mkdir(parents=True)
    ws=Workspace(root/"workspace")
    f=fixture(root,ws,duration,portrait=True)
    cfg=read_json(f["workflow"])
    cfg["render"]["segment_seconds"]=10
    write_json(f["workflow"],cfg)
    started=time.monotonic()
    task=create_video_task(ws,"synthetic-render",f["workflow"],script=f["text"])
    result=TaskManager(ws).run(task["task_id"],observer=TerminalProgress())
    summary={"kind":"SYNTHETIC ENGINEERING ONLY","real_tts":False,"real_lip_sync":False,"style_quality":"not_evaluated",
             "task_id":task["task_id"],"workspace":str(ws.root),"status":result["status"],"error":result.get("error"),
             "elapsed_seconds":time.monotonic()-started,"human_review":"pending"}
    if result["status"]=="completed":
        final=next(s for s in result["stages"] if s["id"]=="final_check")["result"]["video"]
        summary.update(video=final,verification=str(Path(final).parent/"verification.json"))
    write_json(root/"engineering-validation.json",summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",type=Path,default=Path("workspace/v3-06"))
    parser.add_argument("--real-suite",type=Path,help="显式运行收费 TTS + 本地模型；真实等级套件 JSON")
    parser.add_argument("--engineering",action="store_true",help="生成明确标记的合成测试图和正弦音 MP4")
    parser.add_argument("--duration",type=float,default=15)
    args=parser.parse_args()
    if bool(args.real_suite)==args.engineering:
        parser.error("必须明确选择 --engineering 或 --real-suite")
    try:
        result=real_suite(Workspace(args.workspace),args.real_suite) if args.real_suite else engineering(args.workspace,args.duration)
    except Exception as exc:
        result={"status":"blocked","error":str(exc),"real_levels_passed":False}
        write_json(args.workspace/"integration-blocked.json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get("status")=="completed" or result.get("passed") else 1


if __name__=="__main__": raise SystemExit(main())
