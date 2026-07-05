"""Agent 侧 DynamoDB 读写:配置/排期(读)、FleetState/InstanceInventory/MetricsRollup/Audit(写)。"""
from __future__ import annotations
import time
import boto3
from .config import CFG

_res = None


def _t(name: str):
    global _res
    if _res is None:
        _res = boto3.resource("dynamodb", region_name=CFG.aws_region)
    return _res.Table(name)


def get_config() -> dict:
    try:
        r = _t(CFG.table_config).get_item(Key={"config_id": "default"})
        return r.get("Item", {})
    except Exception:  # noqa: BLE001
        return {}


def list_schedules() -> list[dict]:
    try:
        return _t(CFG.table_schedules).scan().get("Items", [])
    except Exception:  # noqa: BLE001
        return []


def put_fleet_state(region: str, kind: str, desired: int, healthy: int):
    try:
        _t(CFG.table_fleetstate).put_item(Item={
            "region": region, "asg_kind": kind, "desired": desired, "healthy": healthy,
            "updated_at": int(time.time()),
        })
    except Exception as e:  # noqa: BLE001
        print(f"[state] put_fleet_state err: {e}", flush=True)


def put_instances(region: str, instances: list[dict]):
    ttl = int(time.time()) + 30 * 86400  # 保留 30 天:实例(含已终止 + 开机/终止时间)历史可回看
    try:
        t = _t(CFG.table_instanceinventory)
        with t.batch_writer() as bw:
            for i in instances:
                item = {k: v for k, v in i.items() if v is not None}  # DynamoDB 不接受 None(如 launch_time 缺失)
                item.update({"region": region, "ttl": ttl, "updated_at": int(time.time())})
                bw.put_item(Item=item)
    except Exception as e:  # noqa: BLE001
        print(f"[state] put_instances err: {e}", flush=True)


def put_metrics(instance_id: str, metrics: dict):
    try:
        _t(CFG.table_metricsrollup).put_item(Item={
            "instance_id": instance_id, "ts": int(time.time()),
            "ttl": int(time.time()) + CFG.retention_days * 86400,  # 监控留存 90 天
            **metrics,
        })
    except Exception as e:  # noqa: BLE001
        print(f"[state] put_metrics err: {e}", flush=True)


def add_running_hours(region: str, spot_hours: float, od_hours: float):
    """把本 tick 的增量运行时长原子累加到「本区 × 当天(UTC+8)」。累加增量,重启不会重复计。"""
    if spot_hours <= 0 and od_hours <= 0:
        return
    from decimal import Decimal
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))  # UTC+8(Asia/Shanghai)天,与页面显示一致
    try:
        # 不设 TTL —— 运行时长历史全部保留,供监控查历史
        _t(CFG.table_costrollup).update_item(
            Key={"region": region, "date": day},
            UpdateExpression="ADD spot_hours :s, od_hours :o SET updated_at = :u",
            ExpressionAttributeValues={
                ":s": Decimal(str(round(spot_hours, 6))), ":o": Decimal(str(round(od_hours, 6))),
                ":u": int(time.time()),
            },
        )
    except Exception as e:  # noqa: BLE001
        print(f"[state] add_running_hours err: {e}", flush=True)


def record_spot_event(region: str, instance_id: str, event_type: str,
                      az: str | None = None, instance_type: str | None = None, reason: str | None = None):
    """记录 Spot 回收/中断事件,TTL 90 天。event_type: interruption-warning | rebalance | reclaimed。"""
    ts = int(time.time())
    try:
        _t(CFG.table_spotevents).put_item(Item={
            "region": region, "ts": ts, "instance_id": instance_id, "event_type": event_type,
            "az": az, "instance_type": instance_type, "reason": reason,
            "ttl": ts + CFG.retention_days * 86400,
        })
    except Exception as e:  # noqa: BLE001
        print(f"[state] record_spot_event err: {e}", flush=True)


def write_audit(audit: dict):
    """落结构化审计。标量原样存(数字保持数字、bool 保持 bool),None 跳过,dict 兜底转字符串。"""
    try:
        item = {
            "date": time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600)),  # UTC+8 天,与筛选/显示一致
            "ts_uuid": f"{int(time.time() * 1000)}#{audit.get('id', '')}",
        }
        for k, v in audit.items():
            if v is None:
                continue
            item[k] = str(v) if isinstance(v, (dict, list)) else v
        _t(CFG.table_actionsaudit).put_item(Item=item)
    except Exception as e:  # noqa: BLE001
        print(f"[state] write_audit err: {e}", flush=True)
