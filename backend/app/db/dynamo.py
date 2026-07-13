"""DynamoDB 访问。manager 模式改编自 sample-bedrock-api-proxy/app/db/dynamodb.py。"""
from __future__ import annotations
import time
from typing import Any
import boto3
from ..core.config import get_settings

# 区域注册表基线(label + 优先级单一来源;须与 agent control_loop.PRIORITY、前端 common.tsx 对齐)。
# priority 升序 = 越优先(0 最高)。Config.regions 为空时用它播种;已有区缺 label/priority 时读时补齐。
DEFAULT_REGIONS: dict[str, dict[str, Any]] = {
    "eu-north-1": {"label": "斯德哥尔摩", "priority": 0},
    "us-east-1": {"label": "弗吉尼亚", "priority": 1},
    "us-east-2": {"label": "俄亥俄", "priority": 2},
    "us-west-2": {"label": "俄勒冈", "priority": 3},
}


def _seed_regions() -> dict[str, Any]:
    """空 Config 时的基线区域集(仅 label/priority/enabled,无 AMI —— 未 provision)。"""
    return {r: {"label": v["label"], "priority": v["priority"], "enabled": True}
            for r, v in DEFAULT_REGIONS.items()}


def _backfill_regions(regions: dict[str, Any]) -> dict[str, Any]:
    """对已有区补齐缺失的 label/priority/enabled —— 只补缺失,绝不覆盖 ami_arn/provisioned_vpc 等。"""
    for r, rc in regions.items():
        if not isinstance(rc, dict):
            continue
        d = DEFAULT_REGIONS.get(r, {})
        rc.setdefault("label", d.get("label", r))
        rc.setdefault("priority", d.get("priority", 99))
        rc.setdefault("enabled", True)
    return regions


class Dynamo:
    def __init__(self):
        s = get_settings()
        self.res = boto3.resource("dynamodb", region_name=s.aws_region)
        self.s = s

    def _t(self, name: str):
        return self.res.Table(name)

    # ---- Config(每区 AMI ARN / 基础台数 / 时区等) ----
    def get_config(self, config_id: str = "default") -> dict[str, Any]:
        r = self._t(self.s.table_config).get_item(Key={"config_id": config_id})
        item = r.get("Item")
        default_model = "global.anthropic.claude-sonnet-4-6"
        default_types = ["p4d.24xlarge", "p4de.24xlarge"]  # 机型优先级(可配置):p4d 优先、p4de 次之
        if not item:
            return {"config_id": config_id, "regions": _seed_regions(), "base_count": 0,
                    "agent_enabled": True, "agent_model_id": default_model,
                    "instance_type_priority": default_types}
        item.setdefault("regions", {})
        # 区域注册表:空则播种基线 4 区;非空则读时补齐缺失的 label/priority/enabled(不动已存字段)
        item["regions"] = _backfill_regions(item["regions"]) if item["regions"] else _seed_regions()
        item.setdefault("base_count", 0)
        item.setdefault("agent_enabled", True)
        item.setdefault("agent_model_id", default_model)
        item.setdefault("instance_type_priority", default_types)
        return item

    def put_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        """局部合并:只更新 patch 中的键;regions 按区深合并,避免向导与排期互相覆盖。"""
        cur = self.get_config()
        merged = {**cur, **patch}
        if "regions" in patch and patch["regions"] is not None:
            regions = {**(cur.get("regions") or {})}
            for reg, rc in patch["regions"].items():
                regions[reg] = {**(regions.get(reg) or {}), **(rc or {})}
            merged["regions"] = regions
        merged["config_id"] = "default"
        merged["updated_at"] = int(time.time())
        self._t(self.s.table_config).put_item(Item=merged)
        return merged

    def delete_config_region(self, region: str) -> dict[str, Any]:
        """从区域注册表删除一个区(put_config 的深合并删不掉 key,故用专用方法)。
        仅删注册表项;不拆该区 AWS 资源(ASG/ALB/VPC/GA endpoint group 需另行清理)。"""
        cur = self.get_config()
        regions = {**(cur.get("regions") or {})}
        regions.pop(region, None)
        cur["regions"] = regions
        cur["config_id"] = "default"
        cur["updated_at"] = int(time.time())
        self._t(self.s.table_config).put_item(Item=cur)
        # 顺带清理该区遗留的 FleetState 行(否则实例队列/概览仍显示已删区,PK=region+asg_kind)
        for kind in ("od", "spot"):
            try:
                self._t(self.s.table_fleetstate).delete_item(Key={"region": region, "asg_kind": kind})
            except Exception:  # noqa: BLE001
                pass
        return cur

    # ---- Schedules(活动窗口排期) ----
    def list_schedules(self) -> list[dict]:
        return self._t(self.s.table_schedules).scan().get("Items", [])

    def put_schedule(self, item: dict[str, Any]) -> dict[str, Any]:
        item.setdefault("schedule_id", "default")
        item["updated_at"] = int(time.time())
        self._t(self.s.table_schedules).put_item(Item=item)
        return item

    # ---- FleetState(各区 spot/od desired/healthy) ----
    def list_fleet_state(self) -> list[dict]:
        return self._t(self.s.table_fleetstate).scan().get("Items", [])

    def put_schedule_delete(self, schedule_id: str) -> None:
        self._t(self.s.table_schedules).delete_item(Key={"schedule_id": schedule_id})

    # ---- FleetState / Runs ----
    def list_runs(self, limit: int = 20) -> list[dict]:
        items = self._t(self.s.table_runs).scan().get("Items", [])
        items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return items[:limit]

    # ---- InstanceInventory ----
    def list_instances(self, region: str | None = None) -> list[dict]:
        t = self._t(self.s.table_instanceinventory)
        if region:
            from boto3.dynamodb.conditions import Key
            return t.query(KeyConditionExpression=Key("region").eq(region)).get("Items", [])
        return t.scan().get("Items", [])

    # ---- MetricsRollup(每台 QPS / latency / tokens,TTL) ----
    def list_metrics(self, limit: int = 500) -> list[dict]:
        items = self._t(self.s.table_metricsrollup).scan(Limit=limit).get("Items", [])
        items.sort(key=lambda x: x.get("ts", 0), reverse=True)
        return items

    def latest_metrics_per_instance(self) -> list[dict]:
        latest: dict[str, dict] = {}
        for m in self.list_metrics():
            iid = m.get("instance_id")
            if iid and (iid not in latest or m.get("ts", 0) > latest[iid].get("ts", 0)):
                latest[iid] = m
        return list(latest.values())

    # ---- ActionsAudit(规则 / Agent 每次变更) ----
    def list_actions(self, limit: int = 100) -> list[dict]:
        items = self._t(self.s.table_actionsaudit).scan().get("Items", [])
        items.sort(key=lambda x: x.get("ts_uuid", ""), reverse=True)
        return items[:limit]

    def list_actions_by_date(self, date: str, limit: int = 1000) -> list[dict]:
        """按天高效 query(date 是 PK),降序返回。"""
        from boto3.dynamodb.conditions import Key
        try:
            r = self._t(self.s.table_actionsaudit).query(
                KeyConditionExpression=Key("date").eq(date), ScanIndexForward=False, Limit=limit)
            return r.get("Items", [])
        except Exception:  # noqa: BLE001
            return []

    # ---- SpotEvents(Spot 回收/中断事件,TTL 90 天) ----
    def list_spot_events(self, limit: int = 1000) -> list[dict]:
        try:
            items = self._t(self.s.table_spotevents).scan(Limit=limit).get("Items", [])
        except Exception:  # noqa: BLE001  表未建/权限滞后于部署时返回空
            return []
        items.sort(key=lambda x: x.get("ts", 0), reverse=True)
        return items

    # ---- CostRollup(每区每天 running hours,TTL 90 天) ----
    def list_cost_rollup(self, limit: int = 500) -> list[dict]:
        try:
            return self._t(self.s.table_costrollup).scan(Limit=limit).get("Items", [])
        except Exception:  # noqa: BLE001
            return []

    # ---- NetworkSelections(每区 VPC/子网/SG/key 选择) ----
    def list_network(self) -> list[dict]:
        return self._t(self.s.table_networkselections).scan().get("Items", [])

    def get_network(self, region: str) -> dict:
        r = self._t(self.s.table_networkselections).get_item(Key={"region": region})
        return r.get("Item", {})

    def put_network(self, item: dict[str, Any]) -> dict[str, Any]:
        item["updated_at"] = int(time.time())
        self._t(self.s.table_networkselections).put_item(Item=item)
        return item


_dynamo: Dynamo | None = None


def get_dynamo() -> Dynamo:
    global _dynamo
    if _dynamo is None:
        _dynamo = Dynamo()
    return _dynamo
