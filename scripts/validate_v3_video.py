"""Reproducible local synthetic media validation. Never overwrite input fixtures."""

import argparse
import json
import subprocess
from pathlib import Path

from creator_dna.storage import Workspace, digest_file, write_json, write_text
from creator_dna.video import LocalVideoAnalyzer
from creator_dna.video_assets import VideoAssetWorkflow


def main():
    parser = argparse.ArgumentParser(description="V3 合成视频工程验证；不是创作者视觉质量验证")
    parser.add_argument("--input", type=Path, required=True, help="新建合成素材目录，拒绝覆盖")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    folder = args.input.resolve()
    workspace = Workspace(args.workspace)
    if folder.exists():
        parser.error("输入目标已存在；请使用新的合成素材路径，禁止覆盖原素材。")
    if folder == workspace.root or folder in workspace.root.parents or workspace.root in folder.parents:
        parser.error("合成素材与 workspace 须互不包含。")
    base = workspace.root / "synthetic-intermediate.mp4"
    if base.exists():
        parser.error("验证中间文件已存在；请选择新的 workspace。")
    analyzer = LocalVideoAnalyzer(sample_interval=0.5, max_frames=24)
    analyzer.dependencies()
    folder.mkdir(parents=True)

    def ffmpeg(arguments):
        # -n prevents accidental overwriting; all files are newly created fixtures.
        analyzer.command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n", *arguments])

    ffmpeg(["-f", "lavfi", "-i", "color=red:s=320x180:r=25:d=1",
            "-f", "lavfi", "-i", "color=blue:s=320x180:r=25:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / "synthetic-landscape.mp4")])
    ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=180x320:r=30000/1001:d=2",
            "-c:v", "libx264", str(folder / "synthetic-portrait.mp4")])
    # Keep the unrotated intermediate in the validation workspace, outside input.
    workspace.root.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=240x180:r=25:d=1", "-vf", "setsar=2/1",
            "-c:v", "libx264", str(base)])
    ffmpeg(["-display_rotation", "90", "-i", str(base), "-c", "copy", str(folder / "synthetic-rotated.mp4")])
    (folder / "synthetic-broken.mov").write_bytes(b"synthetic invalid video fixture")
    write_text(folder / "SYNTHETIC.txt", "全部为本地合成工程测试文件，无真实人像或真实声音；不代表创作者风格质量。\n")
    before = {p.name: {"sha256": digest_file(p), "mtime_ns": p.stat().st_mtime_ns} for p in folder.iterdir()}
    result = VideoAssetWorkflow(workspace, analyzer).analyze(folder, "v3-synthetic")
    after = {p.name: {"sha256": digest_file(p), "mtime_ns": p.stat().st_mtime_ns} for p in folder.iterdir()}
    assert before == after, "合成源文件被改动"
    assert result["status"] == "partial" and result["summary"]["analyzed_count"] == 3
    library = json.loads(Path(result["library"]).read_text(encoding="utf-8"))
    landscape = next(s for s in library["sources"] if s["relative_path"] == "synthetic-landscape.mp4")
    assert len(landscape["cuts"]) == 1 and abs(landscape["cuts"][0]-1) < 0.04
    assert len(library["output_templates"]) == 3
    evidence = {"synthetic_only": True, "original_files_unchanged": before == after,
                "before": before, "after": after, "result": result}
    write_json(Path(result["run"]) / "validation.json", evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
