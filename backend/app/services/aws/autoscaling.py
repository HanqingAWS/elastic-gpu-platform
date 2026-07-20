"""每区 1 个 GPU ASG(nlp-od-<region>):100% On-Demand,机型按配置优先级 prioritized。
desired=0,由控制循环在窗口内调 desired。(需求变更 2026-07-04:Spot 抢不到 p4d/p4de → 全按需;
create_spot_asg 仅保留供回退,不再被 provisioner 调用。)"""
from __future__ import annotations
from .session import client

TYPES = ["p4d.24xlarge", "p4de.24xlarge"]  # 兜底默认(实际以 Config.instance_type_priority 为准,p4d 优先)


def _overrides(types: list[str] | None = None):
    return [{"InstanceType": t} for t in (types or TYPES)]


def create_spot_asg(region: str, name: str, lt_id: str, subnet_ids: list[str], tg_arn: str,
                    max_size: int = 8, types: list[str] | None = None, dry_run: bool = True) -> dict:
    types = types or TYPES
    if dry_run:
        return {"planned": f"create SPOT ASG {name}: 100% spot, capacity-optimized-prioritized, "
                           f"types={types}, CapacityRebalance, desired=0, max={max_size}"}
    try:
        client("autoscaling", region).create_auto_scaling_group(
            AutoScalingGroupName=name, MinSize=0, MaxSize=max_size, DesiredCapacity=0,
            VPCZoneIdentifier=",".join(subnet_ids), TargetGroupARNs=[tg_arn],
            CapacityRebalance=True,
            MixedInstancesPolicy={
                "LaunchTemplate": {
                    "LaunchTemplateSpecification": {"LaunchTemplateId": lt_id, "Version": "$Latest"},
                    "Overrides": _overrides(types),
                },
                "InstancesDistribution": {
                    "OnDemandBaseCapacity": 0,
                    "OnDemandPercentageAboveBaseCapacity": 0,  # 100% Spot
                    "SpotAllocationStrategy": "capacity-optimized-prioritized",  # 按 override 顺序偏好 p4de
                },
            },
            Tags=[{"Key": "managed-by", "Value": "nlp-platform", "PropagateAtLaunch": True}],
        )
    except Exception as e:  # noqa: BLE001  幂等:同名 ASG 已存在则复用(支持重试)
        if "AlreadyExists" not in str(e):
            raise
        return {"asg_name": name, "note": "already exists (reused)"}
    return {"asg_name": name}


def create_od_asg(region: str, name: str, lt_id: str, subnet_ids: list[str], tg_arn: str,
                  max_size: int = 8, types: list[str] | None = None, dry_run: bool = True) -> dict:
    types = types or TYPES
    if dry_run:
        return {"planned": f"create ON-DEMAND ASG {name}: 100% on-demand, prioritized, types={types}, desired=0"}
    try:
        client("autoscaling", region).create_auto_scaling_group(
            AutoScalingGroupName=name, MinSize=0, MaxSize=max_size, DesiredCapacity=0,
            VPCZoneIdentifier=",".join(subnet_ids), TargetGroupARNs=[tg_arn],
            MixedInstancesPolicy={
                "LaunchTemplate": {
                    "LaunchTemplateSpecification": {"LaunchTemplateId": lt_id, "Version": "$Latest"},
                    "Overrides": _overrides(types),
                },
                "InstancesDistribution": {
                    "OnDemandBaseCapacity": 0,
                    "OnDemandPercentageAboveBaseCapacity": 100,  # 100% On-Demand
                    "OnDemandAllocationStrategy": "prioritized",
                },
            },
            Tags=[{"Key": "managed-by", "Value": "nlp-platform", "PropagateAtLaunch": True}],
        )
    except Exception as e:  # noqa: BLE001  幂等:同名 ASG 已存在则复用(支持重试)
        if "AlreadyExists" not in str(e):
            raise
        return {"asg_name": name, "note": "already exists (reused)"}
    return {"asg_name": name}


def update_asg_types(region: str, name: str, lt_id: str, types: list[str], dry_run: bool = True) -> dict:
    """就地更新已存在 ASG 的机型优先级(MixedInstancesPolicy overrides)。
    机型优先级改配置后,重跑「创建资源」(幂等复用路径)即生效,无需删建 ASG。全按需。"""
    if dry_run:
        return {"planned": f"update {name} overrides -> {types} (100% on-demand, prioritized)"}
    client("autoscaling", region).update_auto_scaling_group(
        AutoScalingGroupName=name,
        MixedInstancesPolicy={
            "LaunchTemplate": {
                "LaunchTemplateSpecification": {"LaunchTemplateId": lt_id, "Version": "$Latest"},
                "Overrides": _overrides(types),
            },
            "InstancesDistribution": {
                "OnDemandBaseCapacity": 0,
                "OnDemandPercentageAboveBaseCapacity": 100,  # 100% On-Demand
                "OnDemandAllocationStrategy": "prioritized",
            },
        },
    )
    return {"asg_name": name, "types": types, "updated": True}


def get_asgs(region: str, names: list[str]) -> dict:
    """查这些 ASG 是否存在及其 desired/在册实例数。用于向导判定“资源是否已创建”(ASG 存在即为准)。"""
    try:
        r = client("autoscaling", region).describe_auto_scaling_groups(AutoScalingGroupNames=names)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for g in r.get("AutoScalingGroups", []):
        out[g["AutoScalingGroupName"]] = {
            "desired": g.get("DesiredCapacity", 0), "min": g.get("MinSize", 0),
            "max": g.get("MaxSize", 0), "instances": len(g.get("Instances", []))}
    return out


def start_instance_refresh(region: str, asg_name: str, dry_run: bool = False) -> dict:
    """对 ASG 发起 instance refresh:在跑实例滚动替换为最新 LT($Latest)→ 用上新 AMI。
    desired=0 时无实例可换(近 no-op)。MinHealthyPercentage=0:GPU 稀缺,不强留旧实例。
    best-effort:失败只回错误、不抛(调用方按需展示)。"""
    if dry_run:
        return {"planned": f"instance refresh {asg_name}"}
    try:
        r = client("autoscaling", region).start_instance_refresh(
            AutoScalingGroupName=asg_name,
            Preferences={"MinHealthyPercentage": 0, "InstanceWarmup": 300})
        return {"asg_name": asg_name, "refresh_id": r.get("InstanceRefreshId")}
    except Exception as e:  # noqa: BLE001
        return {"asg_name": asg_name, "error": str(e)}


def set_desired(region: str, asg_name: str, desired: int, dry_run: bool = True) -> dict:
    desired = max(0, min(8, int(desired)))
    if dry_run:
        return {"planned": f"set {asg_name} desired={desired}"}
    client("autoscaling", region).set_desired_capacity(
        AutoScalingGroupName=asg_name, DesiredCapacity=desired, HonorCooldown=False)
    return {"asg_name": asg_name, "desired": desired}
