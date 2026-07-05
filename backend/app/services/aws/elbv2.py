"""区域 ALB(作为 GA endpoint)+ target group(/health,least-outstanding-requests 每台均摊)。"""
from __future__ import annotations
from .session import client

TAGS = [{"Key": "managed-by", "Value": "nlp-platform"}]


def create_alb_with_tg(region: str, vpc_id: str, subnet_ids: list[str], sg_id: str,
                       serving_port: int, health_path: str, dry_run: bool = True) -> dict:
    if dry_run:
        return {"planned": f"create internet-facing ALB @ {vpc_id} subnets={len(subnet_ids)} "
                           f"+ TG(instance,{serving_port},{health_path},LOR) + listener :80"}
    elb = client("elbv2", region)
    alb_name = f"nlp-{region}-{vpc_id[-6:]}"[:32]
    tg_name = f"nlp-tg-{vpc_id[-6:]}"[:32]
    # ALB:名称确定 → 幂等,已存在则查回复用(支持重试)
    try:
        alb = elb.create_load_balancer(
            Name=alb_name, Subnets=subnet_ids, SecurityGroups=[sg_id],
            Scheme="internet-facing", Type="application", Tags=TAGS,
        )["LoadBalancers"][0]
    except Exception as e:  # noqa: BLE001
        if "DuplicateLoadBalancerName" not in str(e):
            raise
        alb = elb.describe_load_balancers(Names=[alb_name])["LoadBalancers"][0]
    # 等待 ALB active 后再注册进 GA(否则 GA 拒绝 provisioning 态的 endpoint)
    try:
        elb.get_waiter("load_balancer_available").wait(
            LoadBalancerArns=[alb["LoadBalancerArn"]], WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    except Exception:  # noqa: BLE001  超时不阻断;若仍未就绪,GA 注册会给出明确错误
        pass
    # Target group:同理幂等
    try:
        tg = elb.create_target_group(
            Name=tg_name, Protocol="HTTP", Port=serving_port, VpcId=vpc_id,
            TargetType="instance", HealthCheckPath=health_path, HealthCheckIntervalSeconds=15,
            HealthyThresholdCount=2, UnhealthyThresholdCount=2,
        )["TargetGroups"][0]
    except Exception as e:  # noqa: BLE001
        if "DuplicateTargetGroupName" not in str(e):
            raise
        tg = elb.describe_target_groups(Names=[tg_name])["TargetGroups"][0]
    # 每台均摊:least_outstanding_requests(可重复调用,幂等)
    elb.modify_target_group_attributes(TargetGroupArn=tg["TargetGroupArn"], Attributes=[
        {"Key": "load_balancing.algorithm.type", "Value": "least_outstanding_requests"},
        {"Key": "deregistration_delay.timeout_seconds", "Value": "60"},
    ])
    # Listener :80 → forward;已存在则忽略(幂等)
    try:
        elb.create_listener(LoadBalancerArn=alb["LoadBalancerArn"], Protocol="HTTP", Port=80,
                            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg["TargetGroupArn"]}])
    except Exception as e:  # noqa: BLE001
        if "DuplicateListener" not in str(e):
            raise
    return {"alb_arn": alb["LoadBalancerArn"], "alb_dns": alb["DNSName"], "tg_arn": tg["TargetGroupArn"]}
