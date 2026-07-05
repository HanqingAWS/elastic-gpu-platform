"""监控 API:每台最新指标 + 全局/分区汇总。数据来源 MetricsRollup(Agent 抓各台 /metrics 写入)。"""
from fastapi import APIRouter
from ..db.dynamo import get_dynamo

router = APIRouter(prefix="/api")


def _num(v, d=0.0) -> float:
    try:
        return float(v)
    except Exception:  # noqa: BLE001
        return d


@router.get("/metrics")
async def metrics():
    d = get_dynamo()
    latest = d.latest_metrics_per_instance()
    inst = {i["instance_id"]: i for i in d.list_instances()}

    per_region: dict[str, dict] = {}
    total_qps = total_tok = 0.0
    lat_samples: list[float] = []
    rows = []
    for m in latest:
        iid = m.get("instance_id", "")
        meta = inst.get(iid, {})
        region = meta.get("region", m.get("region", "-"))
        qps = _num(m.get("qps") or m.get("requests_per_sec"))
        p50 = _num(m.get("latency_p50") or m.get("p50"))
        p95 = _num(m.get("latency_p95") or m.get("p95"))
        tok = _num(m.get("tokens_per_sec") or m.get("token_throughput"))
        total_qps += qps
        total_tok += tok
        if p95:
            lat_samples.append(p95)
        r = per_region.setdefault(region, {"region": region, "qps": 0.0, "tokens_per_sec": 0.0, "nodes": 0})
        r["qps"] += qps
        r["tokens_per_sec"] += tok
        r["nodes"] += 1
        rows.append({
            "instance_id": iid, "region": region, "type": meta.get("type"), "az": meta.get("az"),
            "lifecycle": meta.get("lifecycle"), "health": meta.get("health"),
            "qps": round(qps, 2), "latency_p50": round(p50, 1), "latency_p95": round(p95, 1),
            "tokens_per_sec": round(tok, 1), "ts": m.get("ts"),
        })

    summary = {
        "total_qps": round(total_qps, 2),
        "total_tokens_per_sec": round(total_tok, 1),
        "avg_latency_p95": round(sum(lat_samples) / len(lat_samples), 1) if lat_samples else 0.0,
        "reporting_nodes": len(rows),
    }
    return {"summary": summary, "per_region": list(per_region.values()), "instances": rows}
