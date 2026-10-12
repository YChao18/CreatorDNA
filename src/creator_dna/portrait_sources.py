"""Local raw-video boundary, separate from face/identity/quality inference."""
from pathlib import Path

from .errors import CreatorDNAError
from .input import VIDEO_EXTENSIONS
from .storage import read_json


RAW_PORTRAITS = "raw_portraits"


def raw_folder(input_folder):
    if not input_folder:
        return None
    folder = Path(input_folder).resolve()
    for parent in (folder, *folder.parents):
        if parent.name.casefold() == RAW_PORTRAITS:
            return parent
    child = folder/RAW_PORTRAITS
    # The standard materials/<creator> layout keeps this rule even when the
    # raw folder is missing; removing it must not re-enable edited references.
    return child if child.is_dir() or folder.parent.name.casefold() == "materials" else None


def creator_raw_folder(workspace, creator):
    for name in ("manifest.json", "voice-assets/default.json"):
        path = workspace.creator(creator)/name
        if path.is_file():
            folder = raw_folder(read_json(path).get("input_folder"))
            if folder is not None:
                return folder
    return None


def allowed_video(path, folder):
    path = Path(path)
    return (not path.is_symlink() and path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            and folder.resolve() in path.resolve().parents)


def portrait_sources(library, folder=None):
    folder = folder or raw_folder(library.get("input_folder"))
    if folder is None:
        return library["sources"]
    selected = [s for s in library["sources"] if s["status"] == "analyzed" and allowed_video(s["path"], folder)]
    if not selected:
        raise CreatorDNAError(f"原始人像视频未准备或素材库未更新：{folder}。请放入未经剪辑、无字幕的动态原片，"
                              "再对该目录运行 analyze-video 并更新 portrait.library；不会回退使用剪辑视频。")
    return selected


def check_reused_portraits(assets, folder):
    if folder is None:
        return
    for asset in assets:
        if asset["kind"] != "portrait":
            continue
        proof = asset.get("portrait_provenance", {})
        if proof.get("reference_type", "video") != "video" or not allowed_video(proof.get("source_path", ""), folder):
            raise CreatorDNAError(f"人物参考必须来自 {folder} 内的动态原片；不能复用剪辑视频或静态截图生成的人像。")
