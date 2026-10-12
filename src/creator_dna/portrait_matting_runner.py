"""Run RVM in its isolated model environment; keep the visible upper body.

Usage: model-python portrait_matting_runner.py processor.json request.json
No source editing, identity generation, body stretching or face-only fallback.
"""
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def process(config_path, request_path):
    import cv2
    import numpy as np
    import torch

    cfg = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    request = json.loads(Path(request_path).read_text(encoding="utf-8-sig"))
    source, target = Path(request["input"]), Path(request["output"])
    if request["required_framing"] != "upper_body" or sha(source) != request["input_sha256"]:
        raise RuntimeError("PortraitInputChangedOrWrongFraming")
    if cfg["source_sha256"] != request["source_lock"]["source_sha256"]:
        raise RuntimeError("UpperBodyROIForDifferentSource")
    cap = cv2.VideoCapture(str(source).removeprefix("\\\\?\\"))
    if not cap.isOpened():
        raise RuntimeError("PortraitVideoNotReadable")
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x1,y1,x2,y2 = cfg["upper_body_roi"]
    if not 0 <= x1 < x2 <= w or not 0 <= y1 < y2 <= h:
        raise RuntimeError("UpperBodyROIOutsideSource")
    # Reject the old head/neck framing. Actual full-body framing must also be
    # checked visually; this geometric check alone is not a quality verdict.
    fx,fy,fw,fh = cfg["face_anchor"]
    if y2 < fy+3.0*fh or x2-x1 < 3.0*fw or y1 > fy:
        raise RuntimeError("UpperBodyROICropsTorsoOrShoulders")
    repository = Path(cfg["repository"])
    sys.path.insert(0,str(repository))
    from model import MattingNetwork
    weight = repository/"rvm_mobilenetv3.pth"
    net = MattingNetwork("mobilenetv3").eval().cuda()
    net.load_state_dict(torch.load(weight,map_location="cpu",weights_only=True))
    width,height = x2-x1,y2-y1
    fps = request["frame_rate"]
    wanted = math.ceil(request["duration"]*fps-1e-9)
    source_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if abs(source_frames-wanted) > 1:
        raise RuntimeError("PortraitFrameCoverageMismatch")
    command = [cfg.get("ffmpeg","ffmpeg"),"-v","error","-nostdin","-n","-f","rawvideo","-pix_fmt","rgba",
               "-s",f"{width}x{height}","-r",str(fps),"-i","pipe:0","-an","-c:v","prores_ks",
               "-profile:v","4","-pix_fmt","yuva444p10le","-vf","setsar=1","-threads","2",str(target)]
    encoder = subprocess.Popen(command,stdin=subprocess.PIPE)
    recurrent = [None]*4
    last = None
    samples = []
    try:
        with torch.inference_mode():
            for i in range(wanted):
                ok,frame = cap.read()
                if not ok:
                    if i != wanted-1 or last is None:
                        raise RuntimeError("UnexpectedPortraitFrameGap")
                    frame = last
                last = frame
                rgb = cv2.cvtColor(frame[y1:y2,x1:x2],cv2.COLOR_BGR2RGB)
                tensor = torch.from_numpy(rgb).permute(2,0,1).unsqueeze(0).cuda().float()/255
                fgr,pha,*recurrent = net(tensor,*recurrent,downsample_ratio=cfg.get("downsample_ratio",.5))
                foreground = fgr[0].permute(1,2,0).cpu().numpy()
                alpha = pha[0,0].cpu().numpy()
                # Optional masks only exclude packaging outside the body ROI.
                # They cannot erase/paint clothes, arms, hands or facial pixels.
                for rect in cfg.get("background_exclusion_rects",[]):
                    ax,ay,bx,by=rect
                    alpha[ay:by,ax:bx]=0
                rgba = np.dstack([np.clip(foreground*255,0,255).astype(np.uint8),np.clip(alpha*255,0,255).astype(np.uint8)])
                encoder.stdin.write(rgba.tobytes())
                if i%50 == 0:
                    print(json.dumps({"phase":"upper_body_matting","completed":i+1,"total":wanted}),flush=True)
                    samples.append({"frame":i,"opaque_fraction":float(np.mean(alpha>.9))})
    finally:
        cap.release()
        encoder.stdin.close()
        code=encoder.wait()
    if code or sha(source) != request["input_sha256"]:
        raise RuntimeError("PortraitEncodingFailedOrInputChanged")
    report = {"framing":"upper_body","source_lock":request["source_lock"],"input_sha256":request["input_sha256"],
              "output_sha256":sha(target),"model":"RVM mobilenetv3","weight_sha256":sha(weight),
              "upper_body_roi":cfg["upper_body_roi"],"face_anchor":cfg["face_anchor"],"frames":wanted,
              "original_body_motion_preserved":True,"body_stretched":False,"alpha_samples":samples,
              "perceptual_quality":"not_proven_by_geometry"}
    target.with_suffix(".processing.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    process(sys.argv[1],sys.argv[2])
