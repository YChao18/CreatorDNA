"""Progress based on measured work, never on time spent waiting."""

import math

from .errors import CreatorDNAError


def progress(completed=None, total=None, unit=None, basis="provider status", estimated=False):
    for value in (completed, total):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise CreatorDNAError("进度计数须为非负有限数值。")
    if total is not None and (total <= 0 or completed is None or completed > total):
        raise CreatorDNAError("进度须满足 0 ≤ completed ≤ total 且 total > 0。")
    return {"completed": completed, "total": total, "unit": unit,
            "percent": completed / total * 100 if total is not None else None,
            "estimated": estimated, "basis": basis}


class FFmpegProgressParser:
    """Parse -progress key=value blocks; out_time_ms is microseconds too.

    progress=end only ends a progress block/stream. Successful process exit and
    output verification are still required before completing a stage.
    """

    def __init__(self, duration=None):
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                     or not math.isfinite(duration) or duration <= 0):
            raise CreatorDNAError("FFmpeg 目标时长须为正有限数值。")
        self.duration = duration
        self.block = {}
        self.seconds = 0.0

    def feed(self, line):
        if "=" not in line:
            return None
        key, value = line.strip().split("=", 1)
        self.block[key] = value
        if key != "progress":
            return None
        seconds = None
        for field in ("out_time_us", "out_time_ms"):
            try:
                seconds = int(self.block[field]) / 1_000_000
                break
            except (KeyError, ValueError):
                pass
        if seconds is None:
            try:
                h, m, s = self.block["out_time"].split(":")
                seconds = int(h)*3600 + int(m)*60 + float(s)
            except (KeyError, ValueError):
                pass
        if seconds is not None and math.isfinite(seconds):
            self.seconds = max(self.seconds, seconds, 0)
        self.block = {}
        return progress(min(self.seconds, self.duration) if self.duration else self.seconds,
                        self.duration, "media_seconds", "FFmpeg out_time / target duration" if self.duration
                        else "FFmpeg out_time; target duration unknown")
