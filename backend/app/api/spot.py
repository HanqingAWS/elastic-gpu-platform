"""Spot 回收统计 API:读取 SpotEvents(中断预警 / 再平衡建议 / 被回收终止),按区/类型/日期汇总。
数据 TTL 90 天(Agent 观测到即写入,含 ttl=now+90d)。"""
import time
from fastapi import APIRouter
from ..core.config import get_settings
from ..db.dynamo import get_dynamo

router = APIRouter(prefix="/api")


@router.get("/spot-events")
async def spot_events():
    d = get_dynamo()
    s = get_settings()
    events = d.list_spot_events()

    by_region: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_day: dict[str, int] = {}
    for e in events:
        by_region[e.get("region", "-")] = by_region.get(e.get("region", "-"), 0) + 1
        et = e.get("event_type", "interruption")
        by_type[et] = by_type.get(et, 0) + 1
        ts = int(e.get("ts", 0) or 0)
        day = time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "-"
        by_day[day] = by_day.get(day, 0) + 1

    window = 30 * 86400
    now = int(time.time())
    recent = sum(1 for e in events if int(e.get("ts", 0) or 0) >= now - window)

    return {
        "retention_days": s.retention_days,
        "summary": {
            "total": len(events),
            "last_30d": recent,
            "by_region": [{"region": k, "count": v} for k, v in sorted(by_region.items())],
            "by_type": [{"type": k, "count": v} for k, v in sorted(by_type.items())],
            "by_day": [{"day": k, "count": v} for k, v in sorted(by_day.items(), reverse=True)][:30],
        },
        "events": events[:200],
    }
