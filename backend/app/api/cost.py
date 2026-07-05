"""运行时长 / 计费(兜底)API:每区每天(UTC)累计的 spot/od running hours + 估算成本。
运行时长来自 Agent 每 tick 的原子累加(CostRollup,TTL 90 天),不依赖 Spot 定价。
估算成本 = 时长 × 费率(spot 取实时 describe-spot-price-history,OD 用固定费率),仅供参考。"""
import time
from fastapi import APIRouter, Query
from ..db.dynamo import get_dynamo
from ..services.aws import ec2

router = APIRouter(prefix="/api")

# On-Demand 参考费率(us-east-1,USD/小时,近似):估算用,精确账单以 Cost Explorer 为准
OD_RATES = {"p4de.24xlarge": 40.9657, "p4d.24xlarge": 32.7726}
ASSUMED_TYPE = "p4de.24xlarge"


def _f(v) -> float:
    try:
        return float(v)
    except Exception:  # noqa: BLE001
        return 0.0


@router.get("/running-hours")
async def running_hours(from_: str | None = Query(None, alias="from"), to: str | None = None):
    items = get_dynamo().list_cost_rollup()
    today = time.strftime("%Y-%m-%d", time.gmtime())

    per_today: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    for it in items:
        region = it.get("region", "-")
        day = it.get("date", "-")
        sh, oh = _f(it.get("spot_hours")), _f(it.get("od_hours"))
        if day == today:
            t = per_today.setdefault(region, {"region": region, "spot_hours": 0.0, "od_hours": 0.0})
            t["spot_hours"] += sh
            t["od_hours"] += oh
        d = by_day.setdefault(day, {"date": day, "spot_hours": 0.0, "od_hours": 0.0})
        d["spot_hours"] += sh
        d["od_hours"] += oh

    all_days = sorted(by_day.values(), key=lambda x: x["date"], reverse=True)
    for x in all_days:
        x["spot_hours"] = round(x["spot_hours"], 2)
        x["od_hours"] = round(x["od_hours"], 2)
    for t in per_today.values():
        t["spot_hours"] = round(t["spot_hours"], 2)
        t["od_hours"] = round(t["od_hours"], 2)

    # 日期区间筛选(YYYY-MM-DD,UTC,含端点);无筛选时展示全量、估算按近 7 天
    ranged = bool(from_ or to)
    def _in(day: str) -> bool:
        return (not from_ or day >= from_) and (not to or day <= to)
    filtered = [x for x in all_days if _in(x["date"])]
    hist = (filtered if ranged else all_days)[:180]
    basis = filtered if ranged else all_days[:7]   # 估算基准:筛选区间 / 近 7 天
    tot_spot = round(sum(x["spot_hours"] for x in basis), 2)
    tot_od = round(sum(x["od_hours"] for x in basis), 2)
    # 全按需模式:估算机型取配置优先级第一位;spot_hours 仅为历史遗留数据,按当时 spot 价估
    prio = get_dynamo().get_config().get("instance_type_priority") or [ASSUMED_TYPE]
    assumed = prio[0] if prio[0] in OD_RATES else ASSUMED_TYPE
    od_rate = OD_RATES[assumed]
    spot_rate = (ec2.latest_spot_price("us-east-1", assumed) or 14.0) if tot_spot > 0 else 0.0
    est_7d = round(tot_spot * spot_rate + tot_od * od_rate, 2)

    return {
        "today": list(per_today.values()),
        "by_day": hist,
        "range": {"from": from_, "to": to, "ranged": ranged},
        "totals": {
            "spot_hours_7d": tot_spot, "od_hours_7d": tot_od, "est_usd_7d": est_7d,
            "spot_rate": spot_rate, "od_rate": round(od_rate, 2), "assumed_type": assumed,
            "basis": "所选区间" if ranged else "近 7 天",
        },
    }
