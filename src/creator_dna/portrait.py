"""Automatic sampled-face selection, optional reviews and deterministic reuse plans."""

import json
import math
import copy

from .contracts import validate
from .lip_sync import LipSyncError, duration_value
from .storage import read_json, validate_id, write_json
from .video_assets import validate_library
from .portrait_sources import portrait_sources


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


def rank_candidates(library, review=None, *, reference_folder=None):
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
    for source in portrait_sources(library, reference_folder):
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
            automatic = review is None and bool(samples) and visibility == 1 and candidate["duration"] >= 5 + GUARD_SECONDS
            result.append({"candidate_id": cid, "source_id": source["source_id"],
                "shot_id": candidate.get("shot_id", cid),
                "source_sha256": source["sha256"], "source_path": source["path"],
                "start": r["safe_start"] if r else candidate["start"],
                "end": r["safe_end"] if r else candidate["end"],
                "display_width": source["metadata"]["display_width"],
                "display_height": source["metadata"]["display_height"],
                "proxy_score": proxy_score, "sampled_single_face_ratio": visibility,
                "sharpness_proxy": sharpness, "quality_score": r["quality_score"] if r else None,
                "eligible": approved or automatic, "review": r,
                "evidence_frame_ids": candidate["evidence_frame_ids"],
                "status": "approved" if approved else "automatic_candidate" if automatic else "not_eligible",
                "selection_basis": "provided_review" if review is not None else "sampled_single_face_proxy",
                "limitations": "自动采样不确认身份、嘴部遮挡、姿态或动作语义；真实模型须逐帧定位，输出效果另行评价。"})
    if set(reviews) - known:
        raise LipSyncError("UnknownReviewCandidate")
    # Contiguous measured windows in one shot may be split for inference. This
    # joins coverage/evidence, never infers identity from a face detection.
    if review is None:
        groups = {}
        for c in result:
            if c["eligible"]:
                groups.setdefault((c["source_id"], c["source_sha256"], c["shot_id"]), []).append(c)
        for members in groups.values():
            runs = []
            for c in sorted(members, key=lambda x: x["start"]):
                if runs and abs(runs[-1][-1]["end"]-c["start"]) < .001:
                    runs[-1].append(c)
                else:
                    runs.append([c])
            for run in runs:
                if len(run) < 2:
                    continue
                window = copy.deepcopy(run[0])
                window.update(candidate_id=run[0]["candidate_id"]+"-window", end=run[-1]["end"],
                              member_candidate_ids=[c["candidate_id"] for c in run],
                              evidence_frame_ids=list(dict.fromkeys(f for c in run for f in c["evidence_frame_ids"])))
                result.append(window)
    return sorted(result, key=lambda c: (-int(c["eligible"]), -(c["quality_score"] or 0),
                                       -c["proxy_score"], c["candidate_id"]))


def plan(ranked, durations, history=(), *, timeline="default", position=0, cooldown=30, max_uses=1, candidate=None, source_id=None):
    """Choose an entire video from one source; a shortage never switches files."""
    history = list(history)
    locks = {e["source_sha256"] for e in history if e["timeline"] == timeline}
    if len(locks) > 1:
        raise LipSyncError("MixedPortraitSourcesInTimeline")
    groups = {}
    for c in ranked:
        if (source_id and c["source_id"] != source_id) or (candidate and c["candidate_id"] != candidate):
            continue
        if locks and c["source_sha256"] not in locks:
            continue
        groups.setdefault((c["source_id"], c["source_sha256"], c["source_path"]), []).append(c)
    for (sid, sha, path), candidates in groups.items():
        try:
            result = _plan_source(candidates, durations, history, timeline=timeline, position=position,
                                  cooldown=cooldown, max_uses=max_uses, candidate=candidate)
        except LipSyncError as exc:
            if exc.error_type != "InsufficientReviewedNonRepeatingMaterial":
                raise
            continue
        result["source_lock"] = {"source_id": sid, "source_sha256": sha, "source_path": path}
        return result
    raise LipSyncError("InsufficientSingleSourcePortraitMaterial")


def _plan_source(ranked, durations, history=(), *, timeline="default", position=0, cooldown=30, max_uses=1, candidate=None):
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
            source_start = c["start"]
            automatic_window = bool(c.get("member_candidate_ids") and not c["review"])
            if automatic_window:
                previous_uses = [e for e in events if e["timeline"] == timeline and e["candidate_id"] == c["candidate_id"]]
                if previous_uses:
                    source_start = max(e["source_start"]+e["duration"] for e in previous_uses)
            if c["end"] - source_start + 1e-6 < duration + GUARD_SECONDS:
                continue
            r = selection_attributes(c)
            used = [e for e in events if e["timeline"] == timeline and (
                (not automatic_window and e["candidate_id"] == c["candidate_id"]) or
                (e["source_sha256"] == c["source_sha256"] and
                 e["source_start"] < source_start+duration-1e-6 and
                 e["source_start"]+e["duration"] > source_start+1e-6) or
                (not automatic_window and e["action_key"] == r["action_key"]))]
            # All reservations count, including interrupted jobs; same task is excluded by caller.
            if len(used) >= max_uses or any(position < e["position"]+e["duration"]+cooldown
                                          and position+duration+cooldown > e["position"] for e in used):
                continue
            if any(e["timeline"] == timeline and position < e["position"]+e["duration"]
                   and position+duration > e["position"] for e in events):
                raise LipSyncError("TimelinePositionOccupied")
            previous = next((e for e in reversed(events) if e["timeline"] == timeline
                             and abs(e["position"]+e["duration"]-position) < .001), None)
            source_continuous = bool(previous and previous["source_sha256"] == c["source_sha256"]
                                     and previous["action_key"] == r["action_key"]
                                     and abs(previous["source_start"]+previous["duration"]-source_start) < .001)
            compatible = not previous or source_continuous or (previous["scene_id"] == r["scene_id"] and
                previous["end_pose"] != "unknown" and previous["end_pose"] == r["start_pose"])
            matches.append((not compatible, c, previous, source_start, source_continuous))
        if not matches:
            raise LipSyncError("InsufficientReviewedNonRepeatingMaterial")
        # Stable ranking; prefer same scene + matching boundary pose, no random choices.
        _, c, previous, source_start, source_continuous = min(matches, key=lambda item: (item[0], ranked.index(item[1])))
        r = selection_attributes(c)
        transition = "continuous" if not previous or source_continuous or (previous["scene_id"] == r["scene_id"]
                      and previous["end_pose"] != "unknown" and previous["end_pose"] == r["start_pose"]) else "b_roll_or_scene_transition_required"
        event = {"timeline": timeline, "candidate_id": c["candidate_id"], "source_sha256": c["source_sha256"],
                 "source_start": source_start, "source_end": source_start+duration+GUARD_SECONDS,
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


def selection_attributes(candidate):
    if candidate["review"]:
        return candidate["review"]
    # Source shot grouping is conservative reuse bookkeeping, not inferred pose
    # or semantic action recognition. Unknown poses never prove continuity.
    return {"scene_id": "unknown", "action_key": candidate["source_sha256"] + ":" + candidate["shot_id"],
            "start_pose": "unknown", "end_pose": "unknown"}


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
