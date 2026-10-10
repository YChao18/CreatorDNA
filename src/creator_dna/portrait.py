"""Evidence-based ranking, manual suitability review and deterministic reuse plans."""

import json
import math

from .contracts import validate
from .lip_sync import LipSyncError, duration_value
from .storage import read_json, validate_id, write_json
from .video_assets import validate_library


GUARD_SECONDS = .2  # Extra source frames for Whisper chunk rounding, never loop.


def load_library(path, creator):
    library = read_json(path)
    validate_library(library)
    if library["creator_id"] != creator:
        raise LipSyncError("CreatorMismatch")
    return library


def review_template(library):
    return {"schema_version": "3.0-04", "creator_id": library["creator_id"],
            "library_run_id": library["run_id"], "candidates": [{
                "candidate_id": c["candidate_id"], "source_sha256": s["sha256"],
                "approved": False, "reviewer": "", "evidence": "",
                "front_facing": False, "face_clear": False, "mouth_unobstructed": False,
                "moderate_head_motion": False, "natural_action": False,
                "quality_score": 0, "scene_id": "unknown", "action_key": "unknown",
                "start_pose": "unknown", "end_pose": "unknown",
                "safe_start": c["start"], "safe_end": c["end"]}
                for s in library["sources"] if s["status"] == "analyzed"
                for c in s["candidates"] if "A" in c["suggested_categories"]]}


def rank_candidates(library, review=None):
    reviews = {}
    if review is not None:
        try:
            json.dumps(review, allow_nan=False)
        except (ValueError, TypeError):
            raise LipSyncError("NonFiniteReviewValue") from None
        validate(review, "portrait-review")
        if review["creator_id"] != library["creator_id"] or review["library_run_id"] != library["run_id"]:
            raise LipSyncError("ReviewLibraryMismatch")
        reviews = {r["candidate_id"]: r for r in review["candidates"]}
        if len(reviews) != len(review["candidates"]):
            raise LipSyncError("DuplicateReview")
    result = []
    known = set()
    for source in library["sources"]:
        if source["status"] != "analyzed":
            continue
        frames = {f["frame_id"]: f for f in source["frames"]}
        for candidate in source["candidates"]:
            cid = candidate["candidate_id"]
            if cid in known:
                raise LipSyncError("DuplicateCandidate")
            known.add(cid)
            r = reviews.get(cid)
            if r and (r["source_sha256"] != source["sha256"] or
                      not candidate["start"] <= r["safe_start"] < r["safe_end"] <= candidate["end"]):
                raise LipSyncError("ReviewEvidenceChanged")
            samples = [frames[fid] for fid in candidate["evidence_frame_ids"]]
            faces = [f["faces"][0] for f in samples if f["faces"] and len(f["faces"]) == 1]
            # These are selection proxies, never pose, identity or mouth judgments.
            visibility = len(faces) / len(samples) if samples else 0
            sharpness = sum(f["sharpness_proxy"] for f in faces) / len(faces) if faces else None
            pixels = source["metadata"]["display_width"] * source["metadata"]["display_height"]
            proxy_score = round(visibility * 50 + min(20, math.log1p(sharpness or 0)*3)
                                + min(20, pixels/1920/1080*20) + min(10, candidate["duration"]), 3)
            approved = bool(r and r["approved"] and all(r[k] for k in (
                "front_facing", "face_clear", "mouth_unobstructed", "moderate_head_motion", "natural_action"))
                and r["reviewer"].strip() and r["evidence"].strip()
                and all(r[k] != "unknown" for k in ("scene_id", "action_key", "start_pose", "end_pose")))
            result.append({"candidate_id": cid, "source_id": source["source_id"],
                "source_sha256": source["sha256"], "source_path": source["path"],
                "start": r["safe_start"] if r else candidate["start"],
                "end": r["safe_end"] if r else candidate["end"],
                "display_width": source["metadata"]["display_width"],
                "display_height": source["metadata"]["display_height"],
                "proxy_score": proxy_score, "sampled_single_face_ratio": visibility,
                "sharpness_proxy": sharpness, "quality_score": r["quality_score"] if r else None,
                "eligible": approved, "review": r,
                "evidence_frame_ids": candidate["evidence_frame_ids"],
                "status": "approved" if approved else "manual_review_required"})
    if set(reviews) - known:
        raise LipSyncError("UnknownReviewCandidate")
    return sorted(result, key=lambda c: (-int(c["eligible"]), -(c["quality_score"] or 0),
                                       -c["proxy_score"], c["candidate_id"]))


def plan(ranked, durations, history=(), *, timeline="default", position=0, cooldown=30, max_uses=1, candidate=None):
    validate_id(timeline)
    if (isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(position) or position < 0
            or isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or not math.isfinite(cooldown) or cooldown < 0
            or type(max_uses) is not int or not 1 <= max_uses <= 10 or not durations):
        raise LipSyncError("InvalidReuseParameters")
    events = list(history)
    selected = []
    for duration in durations:
        duration = duration_value(duration)
        matches = []
        for c in ranked:
            if not c["eligible"] or (candidate and c["candidate_id"] != candidate):
                continue
            if c["end"] - c["start"] + 1e-6 < duration + GUARD_SECONDS:
                continue
            r = c["review"]
            used = [e for e in events if e["timeline"] == timeline and (
                e["candidate_id"] == c["candidate_id"] or
                (e["source_sha256"] == c["source_sha256"] and
                 e["source_start"] < c["start"]+duration+GUARD_SECONDS and e["source_end"] > c["start"]) or
                e["action_key"] == r["action_key"])]
            # All reservations count, including interrupted jobs; same task is excluded by caller.
            if len(used) >= max_uses or any(position < e["position"]+e["duration"]+cooldown
                                          and position+duration+cooldown > e["position"] for e in used):
                continue
            if any(e["timeline"] == timeline and position < e["position"]+e["duration"]
                   and position+duration > e["position"] for e in events):
                raise LipSyncError("TimelinePositionOccupied")
            previous = next((e for e in reversed(events) if e["timeline"] == timeline
                             and abs(e["position"]+e["duration"]-position) < .001), None)
            compatible = not previous or (previous["scene_id"] == r["scene_id"] and previous["end_pose"] == r["start_pose"])
            matches.append((not compatible, c, previous))
        if not matches:
            raise LipSyncError("InsufficientReviewedNonRepeatingMaterial")
        # Stable ranking; prefer same scene + matching boundary pose, no random choices.
        _, c, previous = min(matches, key=lambda item: (item[0], ranked.index(item[1])))
        r = c["review"]
        transition = "continuous" if not previous or (previous["scene_id"] == r["scene_id"]
                      and previous["end_pose"] == r["start_pose"]) else "b_roll_or_scene_transition_required"
        event = {"timeline": timeline, "candidate_id": c["candidate_id"], "source_sha256": c["source_sha256"],
                 "source_start": c["start"], "source_end": c["start"]+duration+GUARD_SECONDS,
                 "position": position, "duration": duration, "action_key": r["action_key"],
                 "scene_id": r["scene_id"], "start_pose": r["start_pose"], "end_pose": r["end_pose"],
                 "transition": transition, "usage_count": len([e for e in events if e["timeline"] == timeline
                     and e["candidate_id"] == c["candidate_id"]])+1}
        selected.append({"candidate": c, "usage": event})
        events.append(event)
        position += duration
    return {"schema_version": "3.0-04", "timeline": timeline, "clips": selected,
            "total_duration": sum(duration_value(d) for d in durations),
            "looping": False, "b_roll_hooks": [{"position": c["usage"]["position"],
                "reason": c["usage"]["transition"]} for c in selected if c["usage"]["transition"] != "continuous"]}


class UsageLedger:
    def __init__(self, workspace, creator):
        self.workspace, self.creator = workspace, creator
        self.path = workspace.creator(creator) / "portrait-assets" / "usage.json"

    def read(self):
        data = read_json(self.path) if self.path.exists() else {"schema_version": "3.0-04", "events": []}
        validate(data, "portrait-usage")
        return data["events"]

    def reserve(self, task_id, selected, ranked, options):
        with self.workspace.lock(self.creator):
            events = self.read()
            old = next((e for e in events if e["task_id"] == task_id), None)
            if old:
                if any(old[k] != v for k, v in selected["usage"].items()):
                    raise LipSyncError("ReservationChanged")
                return  # Idempotent safe local retry, never add another use.
            checked = plan(ranked, [selected["usage"]["duration"]], events,
                           candidate=selected["candidate"]["candidate_id"], **options)["clips"][0]
            if checked != selected:
                raise LipSyncError("ReusePlanChanged")
            events.append({**selected["usage"], "task_id": task_id, "status": "reserved"})
            write_json(self.path, {"schema_version": "3.0-04", "events": events})

    def finish(self, task_id, status, artifact=None):
        with self.workspace.lock(self.creator):
            events = self.read()
            for e in events:
                if e["task_id"] == task_id:
                    e.update(status=status, artifact=artifact)
            write_json(self.path, {"schema_version": "3.0-04", "events": events})
