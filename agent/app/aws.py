"""Agent 侧 AWS 观测 + 动作(boto3,用 ECS task role)。观测只读;动作经 guardrails 封装。"""
from __future__ import annotations
import functools
import boto3


@functools.lru_cache(maxsize=None)
def _c(service: str, region: str):
    return boto3.client(service, region_name=region)


def describe_asg(region: str, name: str) -> dict | None:
    r = _c("autoscaling", region).describe_auto_scaling_groups(AutoScalingGroupNames=[name])
    gs = r.get("AutoScalingGroups", [])
    return gs[0] if gs else None


def asg_target_group_arns(asg: dict) -> list[str]:
    return asg.get("TargetGroupARNs", [])


def healthy_target_count(region: str, tg_arn: str) -> int:
    """经 ALB target health 判定真正就绪的台数(比 ASG InService 更贴近可服务)。"""
    try:
        r = _c("elbv2", region).describe_target_health(TargetGroupArn=tg_arn)
        return sum(1 for t in r["TargetHealthDescriptions"] if t["TargetHealth"]["State"] == "healthy")
    except Exception:  # noqa: BLE001
        return 0


def instance_details(region: str, ids: list[str]) -> dict:
    """EC2 真实开机时间 + 状态(ASG 记录里没有 LaunchTime)。只查当前在册的 id(它们必然存在)。"""
    out: dict[str, dict] = {}
    if not ids:
        return out
    try:
        r = _c("ec2", region).describe_instances(InstanceIds=ids)
        for res in r.get("Reservations", []):
            for i in res.get("Instances", []):
                lt = i.get("LaunchTime")
                out[i["InstanceId"]] = {
                    "launch_time": lt.strftime("%Y-%m-%dT%H:%M:%S+00:00") if lt else None,
                    "ec2_state": (i.get("State") or {}).get("Name"),
                }
    except Exception:  # noqa: BLE001
        pass
    return out


def target_health_map(region: str, tg_arn: str) -> dict:
    """每个实例在 ALB target group 的健康态(healthy/unhealthy/initial/…)。
    用于区分「运行中(EC2/ASG)」与「模型就绪(ALB /health 命中)」。"""
    out: dict[str, str] = {}
    try:
        r = _c("elbv2", region).describe_target_health(TargetGroupArn=tg_arn)
        for t in r["TargetHealthDescriptions"]:
            out[t["Target"]["Id"]] = t["TargetHealth"]["State"]
    except Exception:  # noqa: BLE001
        pass
    return out


def asg_instances(region: str, asg: dict) -> list[dict]:
    out = []
    for i in asg.get("Instances", []):
        out.append({
            "instance_id": i["InstanceId"], "az": i.get("AvailabilityZone"),
            "lifecycle": i.get("LifecycleState"), "health": i.get("HealthStatus"),
            "type": i.get("InstanceType"),
        })
    return out


def recent_spot_interruptions(region: str) -> list[dict]:
    """从控制平面识别被 Spot 回收终止的本平台实例(StateReason=Server.SpotInstanceTermination/Shutdown)。"""
    out: list[dict] = []
    try:
        r = _c("ec2", region).describe_instances(Filters=[
            {"Name": "instance-lifecycle", "Values": ["spot"]},
            {"Name": "tag:managed-by", "Values": ["nlp-platform"]},
            {"Name": "instance-state-name", "Values": ["shutting-down", "terminated"]},
        ])
    except Exception:  # noqa: BLE001
        return out
    for res in r.get("Reservations", []):
        for i in res.get("Instances", []):
            reason = f"{(i.get('StateReason') or {}).get('Code','')} {i.get('StateTransitionReason','')}"
            if "SpotInstanceTermination" in reason or "SpotInstanceShutdown" in reason:
                out.append({
                    "instance_id": i["InstanceId"],
                    "az": (i.get("Placement") or {}).get("AvailabilityZone"),
                    "instance_type": i.get("InstanceType"),
                    "reason": reason.strip(),
                })
    return out


def set_desired(region: str, asg_name: str, desired: int):
    _c("autoscaling", region).set_desired_capacity(
        AutoScalingGroupName=asg_name, DesiredCapacity=max(0, min(8, int(desired))), HonorCooldown=False)


@functools.lru_cache(maxsize=None)
def find_endpoint_group_arn(accelerator_arn: str, region: str) -> str | None:
    """GA 是全局服务,控制端点在 us-west-2。缓存 region->endpoint group arn。"""
    ga = _c("globalaccelerator", "us-west-2")
    try:
        for lst in ga.list_listeners(AcceleratorArn=accelerator_arn)["Listeners"]:
            for eg in ga.list_endpoint_groups(ListenerArn=lst["ListenerArn"])["EndpointGroups"]:
                if eg["EndpointGroupRegion"] == region:
                    return eg["EndpointGroupArn"]
    except Exception:  # noqa: BLE001
        return None
    return None
