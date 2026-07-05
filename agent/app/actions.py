"""有护栏的 AWS 变更动作(不依赖 strands)。规则控制循环与 Agent 工具共用。
每个动作返回结构化明细(kind/before/after/unit/target/status)供审计,由 guardrails 落库。"""
from __future__ import annotations
import boto3
from .tools.guardrails import guarded, clamp_desired


def _asg_kind(name: str) -> str:
    if "-spot-" in name or name.endswith("-spot"):
        return "spot"
    if "-od-" in name or name.endswith("-od"):
        return "od"
    return "asg"


@guarded("set_asg_desired")
def _set_asg_desired(*, region: str, dry_run: bool, asg_name: str, desired: int):
    after = clamp_desired(desired)
    kind = _asg_kind(asg_name)
    if dry_run:
        status = "planned"
    else:
        boto3.client("autoscaling", region_name=region).set_desired_capacity(
            AutoScalingGroupName=asg_name, DesiredCapacity=after, HonorCooldown=False)
        status = "done"
    return {"kind": kind, "target": asg_name, "after": after, "unit": "台", "status": status}


@guarded("trigger_od_backfill")
def _trigger_od_backfill(*, region: str, dry_run: bool, od_asg_name: str, gap: int):
    after = clamp_desired(gap)
    if dry_run:
        status = "planned"
    else:
        boto3.client("autoscaling", region_name=region).set_desired_capacity(
            AutoScalingGroupName=od_asg_name, DesiredCapacity=after, HonorCooldown=False)
        status = "done"
    return {"kind": "od", "target": od_asg_name, "after": after, "unit": "台", "status": status}


@guarded("set_ga_weights")
def _set_ga_weights(*, region: str, dry_run: bool, endpoint_group_arn: str, traffic_dial: int):
    after = max(0, min(100, int(traffic_dial)))
    if dry_run:
        status = "planned"
    else:
        boto3.client("globalaccelerator", region_name="us-west-2").update_endpoint_group(
            EndpointGroupArn=endpoint_group_arn, TrafficDialPercentage=float(after))
        status = "done"
    return {"kind": "ga", "target": endpoint_group_arn.split("/")[-1], "after": after, "unit": "%", "status": status}
