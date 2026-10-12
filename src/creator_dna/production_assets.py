"""Rights-aware local asset inventory. No remote search, downloads or visual inference."""
import shutil
import subprocess
import json
import math
from pathlib import Path

from .contracts import validate
from .errors import CreatorDNAError
from .storage import digest_file
from .video import metadata_from_probe
from .video_assets import validate_library


USABLE_RIGHTS = {"owned", "licensed", "authorized"}


def probe(path, media_type, ffprobe="ffprobe"):
    if media_type == "image":
        try:
            from PIL import Image, ImageOps
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                oriented = ImageOps.exif_transpose(image)
                return oriented.width, oriented.height, None
        except ImportError:
            raise CreatorDNAError('图片素材需要 Pillow：pip install -e ".[video]"') from None
        except (OSError, ValueError) as exc:
            raise CreatorDNAError(f"图片素材测量失败：{path}: {exc}") from exc
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode:
            raise ValueError(result.stderr[-500:])
        m = metadata_from_probe(json.loads(result.stdout))
        return round(m["display_width"]), round(m["display_height"]), m["duration"]
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise CreatorDNAError(f"素材测量失败：{path}: {exc}") from exc


def import_asset(path, asset_id, kind, media_type, source, rights, rights_note, topics, description, ffprobe="ffprobe", audio_offset=None, audio_sha256=None):
    path = Path(path).resolve()
    width, height, duration = probe(path, media_type, ffprobe)
    asset = {"asset_id": asset_id, "kind": kind, "media_type": media_type, "path": str(path),
             "sha256": digest_file(path), "source": source, "rights": rights, "rights_note": rights_note,
             "topics": topics, "description": description, "width": width, "height": height,
             "duration": duration, "source_start": 0, "source_end": duration,
             "audio_offset": (0 if audio_offset is None else audio_offset) if kind == "portrait" else None,
             "audio_sha256": audio_sha256 if kind == "portrait" else None}
    validate_assets({"schema_version": "3.0-05", "assets": [asset]})
    return asset


def from_library(library):
    validate_library(library)
    assets = []
    for source in library["sources"]:
        if source["status"] != "analyzed":
            continue
        m = source["metadata"]
        for c in source["candidates"]:
            assets.append({"asset_id": c["candidate_id"], "kind": "video", "media_type": "video",
               "path": source["path"], "sha256": source["sha256"], "source": source["relative_path"],
               "rights": "unknown", "rights_note": "V3.0-01 测量不包含复用授权，请补充授权依据与人工分类。",
               "topics": [], "description": c["reuse_advice"], "width": round(m["display_width"]),
               "height": round(m["display_height"]), "duration": m["duration"],
               "source_start": c["start"], "source_end": c["end"], "audio_offset": None, "audio_sha256": None})
    return {"schema_version": "3.0-05", "assets": assets}


def validate_assets(registry):
    validate(registry, "production-assets")
    ids = set()
    for a in registry["assets"]:
        if any(v is not None and not math.isfinite(v) for v in
               (a["duration"],a["source_start"],a["source_end"],a["audio_offset"])):
            raise CreatorDNAError("素材包含非有限时间数值。")
        if a["asset_id"] in ids:
            raise CreatorDNAError("素材 ID 重复。")
        ids.add(a["asset_id"])
        if a["media_type"] == "video":
            if a["duration"] is None or a["source_end"] is None or not 0 <= a["source_start"] < a["source_end"] <= a["duration"]:
                raise CreatorDNAError("视频素材可用范围无效。")
        elif a["source_start"] != 0 or a["source_end"] is not None or a["duration"] is not None:
            raise CreatorDNAError("图片素材不可填写伪视频时长。")
        if a["kind"] == "portrait" and a["media_type"] != "video":
            raise CreatorDNAError("人像轨需要可播放视频，本模块不生成静态图口型。")
        if (a["kind"] == "portrait") != (a["audio_offset"] is not None):
            raise CreatorDNAError("人像素材必须声明相对于口播音频的 audio_offset；其他素材为 null。")
        if (a["kind"] == "portrait") != (a["audio_sha256"] is not None):
            raise CreatorDNAError("人像素材必须绑定对应主口播 PCM 音频的哈希；其他素材为 null。")
        if a.get("portrait_provenance"):
            if a["kind"] != "portrait":
                raise CreatorDNAError("仅人像素材可携带人物来源记录。")
            previous = 0
            ranges = []
            for segment in a["portrait_provenance"]["segments"]:
                start,end,ss,se = (segment[k] for k in ("start","end","source_start","source_end"))
                if (not all(math.isfinite(v) for v in (start,end,ss,se)) or abs(start-previous) > 1e-6
                        or end <= start or se <= ss or abs((se-ss)-(end-start)) > 1e-6
                        or (a["portrait_provenance"].get("reference_type","video") == "video" and
                            any(max(ss,s) < min(se,e)-1e-6 for s,e in ranges))):
                    raise CreatorDNAError("人像来源记录存在时间缺口、重复动作区间或时长不一致。")
                ranges.append((ss,se))
                previous=end
            if abs(previous-a["duration"]) > .08:
                raise CreatorDNAError("人像来源记录未覆盖整段素材。")
    return registry


def check_portrait_sources(assets, *, require_provenance=False):
    portraits = [a for a in assets if a["kind"] == "portrait"]
    if require_provenance and any(not a.get("portrait_provenance") for a in portraits):
        raise CreatorDNAError("复用人物必须提供单源来源记录；旧混合人物不能进入新视频。")
    keys = set()
    for a in portraits:
        proof=a.get("portrait_provenance")
        keys.add((proof["source_path"],proof["source_sha256"]) if proof else (a["path"],a["sha256"]))
    if len(keys) > 1:
        raise CreatorDNAError("同一条视频的人物必须来自同一个源文件，禁止跨素材混用。")


def select_asset(assets, role, topics, duration, preferred=None, used=None, timeline_start=0, audio_sha256=None):
    used = used or {}
    ranked = []
    for a in assets:
        if a["rights"] not in USABLE_RIGHTS:
            continue
        if (role == "portrait" and a["kind"] != "portrait") or (role == "b_roll" and a["kind"] in {"portrait", "background"}) or (role == "background" and a["kind"] != "background"):
            continue
        if preferred and a["asset_id"] != preferred:
            continue
        start = used.get(a["asset_id"], a["source_start"])
        if role == "portrait":
            if a["audio_sha256"] != audio_sha256:
                continue
            if timeline_start < a["audio_offset"]:
                continue
            start = a["source_start"] + timeline_start - a["audio_offset"]
        if a["media_type"] == "video" and start + duration > a["source_end"] + 1e-6:
            continue
        matched = sorted({t for t in topics if any(t.casefold() in tag.casefold() or tag.casefold() in t.casefold()
                                                 for tag in a["topics"])})
        if role == "b_roll" and not preferred and not matched:
            continue
        path = Path(a["path"])
        if not path.is_file() or digest_file(path) != a["sha256"]:
            continue
        ranked.append((len(matched), a["asset_id"], a, start, matched))
    if not ranked:
        return None, None, "没有授权、匹配主题且时长足够的本地素材；请补充或修订素材清单。"
    _, _, asset, start, matched = sorted(ranked, key=lambda r: (-r[0], r[1]))[0]
    reason = ("指定素材 " + asset["asset_id"] if preferred else "标签匹配：" + "、".join(matched) if matched else "授权人像/背景候选")
    return asset, start, reason + "；授权依据：" + asset["rights_note"]


def prepare_assets(assets, folder):
    """Copy verified selected local files once per hash. Source files remain read-only."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    for asset in assets:
        source = Path(asset["path"])
        if digest_file(source) != asset["sha256"]:
            raise CreatorDNAError("素材在准备前发生变化。")
        target = folder / (asset["sha256"] + source.suffix.lower())
        if not target.exists():
            shutil.copyfile(source, target)
        if digest_file(target) != asset["sha256"]:
            raise CreatorDNAError("素材副本哈希校验失败。")
        result.append({**asset, "path": str(target.resolve())})
    return result
