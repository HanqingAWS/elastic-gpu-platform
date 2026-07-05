"""Strands @tool 包装(仅供 P4 Agent 决策)。底层动作在 ../actions.py(有护栏、不依赖 strands)。"""
from __future__ import annotations
from strands import tool  # type: ignore
from ..actions import _set_asg_desired, _trigger_od_backfill, _set_ga_weights


@tool
def get_fleet_state() -> dict:
    """返回各区 spot/od ASG 的 desired/healthy 与实例清单(只读)。"""
    from .. import state
    return {"fleet": "读 DynamoDB FleetState(见 UI /api/fleet)", "base_count": state.get_config().get("base_count", 0)}


@tool
def set_asg_desired(region: str, asg_name: str, desired: int, reason: str = "Agent 决策") -> dict:
    """设置某区某 ASG 期望台数(护栏:clamp<=8、冷却、每tick上限、审计)。reason 会记入审计。"""
    return _set_asg_desired(region=region, dry_run=False, source="agent", reason=reason, asg_name=asg_name, desired=desired)


@tool
def trigger_od_backfill(region: str, od_asg_name: str, gap: int, reason: str = "Agent 决策:OD 兜底") -> dict:
    """Spot 不足时用 On-Demand ASG 补差额(护栏)。reason 会记入审计。"""
    return _trigger_od_backfill(region=region, dry_run=False, source="agent", reason=reason, od_asg_name=od_asg_name, gap=gap)


@tool
def set_ga_weights(region: str, endpoint_group_arn: str, traffic_dial: int, reason: str = "Agent 决策:调整权重") -> dict:
    """按各区健康台数占比设置 GA endpoint group TrafficDial(护栏)。reason 会记入审计。"""
    return _set_ga_weights(region=region, dry_run=False, source="agent", reason=reason, endpoint_group_arn=endpoint_group_arn, traffic_dial=traffic_dial)


AGENT_TOOLS = [get_fleet_state, set_asg_desired, trigger_od_backfill, set_ga_weights]
