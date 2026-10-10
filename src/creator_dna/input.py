from dataclasses import dataclass
from pathlib import Path

from .errors import CreatorDNAError
from .storage import digest_file

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}
TEXT_EXTENSIONS = (".json", ".srt", ".txt", ".md")


@dataclass(frozen=True)
class Source:
    path: Path
    relative_path: str
    source_id: str
    sha256: str
    sidecar: Path | None


def validate_input_folder(folder, workspace):
    folder = Path(folder).resolve()
    workspace = Path(workspace).resolve()
    if not folder.is_dir():
        raise CreatorDNAError(f"素材文件夹不存在：{folder}")
    if folder == workspace or folder in workspace.parents or workspace in folder.parents:
        raise CreatorDNAError("素材文件夹与 workspace 须互不包含，以避免生成物被当作输入或修改素材目录。")
    return folder


def scan(folder, workspace, recursive=False):
    folder = validate_input_folder(folder, workspace)
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    paths = sorted((p for p in iterator if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS | set(TEXT_EXTENSIONS)),
                   key=lambda p: p.relative_to(folder).as_posix().casefold())
    if any(p.is_symlink() or folder not in p.resolve().parents for p in paths):
        raise CreatorDNAError("素材包含链接或指向文件夹外的文件，请复制为普通文件后重试。")
    groups = {}
    for path in paths:
        groups.setdefault((path.parent, path.stem.casefold()), []).append(path)
    sources = []
    for members in groups.values():
        media = [p for p in members if p.suffix.lower() in MEDIA_EXTENSIONS]
        texts = [p for p in members if p.suffix.lower() in TEXT_EXTENSIONS]
        if len(media) > 1 or len(texts) > 1:
            raise CreatorDNAError(f"同名素材或转写稿存在歧义：{', '.join(str(p) for p in members)}。每个素材保留一份同名转写稿。")
        path = (media or texts)[0]
        relative = path.relative_to(folder).as_posix()
        import hashlib
        source_id = "s-" + hashlib.sha256(relative.encode()).hexdigest()[:16]
        sources.append(Source(path, relative, source_id, digest_file(path), texts[0] if media and texts else None))
    if not sources:
        raise CreatorDNAError("文件夹中没有支持的视频、音频或转写稿。")
    return folder, sources
