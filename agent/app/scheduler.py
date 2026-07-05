"""时区感知的活动窗口判定。排期项:{schedule_id, start:"17:00", end:"24:00",
timezone:"Asia/Shanghai", activity_count:2, enabled:true}。支持跨午夜 + 预热提前量。
多排期:叠加所有当前(含预热)激活排期的活动数量。总目标 = 基础数量 + 活动数量(在 control_loop 相加)。"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import CFG


def _to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _now_min(tz: str) -> int:
    now = datetime.now(ZoneInfo(tz))
    return now.hour * 60 + now.minute


def evaluate(schedules: list[dict]) -> dict:
    """返回 {window_open, prewarm, activity, schedule_ids}。activity = 所有激活排期活动数量之和。"""
    window_open = False
    prewarm = False
    activity = 0
    ids: list[str] = []
    lead = CFG.prewarm_lead_min
    for s in schedules:
        if not s.get("enabled", True):
            continue
        tz = s.get("timezone", "Asia/Shanghai")
        start = _to_min(s.get("start", "17:00"))
        end = _to_min(s.get("end", "24:00"))
        count = int(s.get("activity_count", s.get("target", CFG.default_target)))
        now = _now_min(tz)

        def in_range(a: int, b: int) -> bool:
            return (a <= now < b) if a < b else (now >= a or now < b)  # 支持跨午夜

        w = in_range(start, end)
        p = in_range((start - lead) % 1440, end)
        if p:                       # 预热区间(含窗口)内即计入活动数量
            activity += count
            prewarm = True
            ids.append(s.get("schedule_id"))
        if w:
            window_open = True
    return {"window_open": window_open, "prewarm": prewarm, "activity": activity, "schedule_ids": ids}
