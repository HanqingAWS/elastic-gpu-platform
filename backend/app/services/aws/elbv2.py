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


# ---- BYO(自带 ALB)+ 发现 + 校验 用的辅助 ----

def list_load_balancers(region: str, vpc_id: str | None = None) -> list[dict]:
    """列 application LB(供向导下拉);vpc_id 给定则只列该 VPC。返回 scheme 供 UI 标注/校验。"""
    elb = client("elbv2", region)
    out = []
    for lb in elb.describe_load_balancers().get("LoadBalancers", []):
        if lb.get("Type") != "application":
            continue
        if vpc_id and lb.get("VpcId") != vpc_id:
            continue
        out.append({"alb_arn": lb["LoadBalancerArn"], "name": lb["LoadBalancerName"], "dns": lb["DNSName"],
                    "scheme": lb["Scheme"], "vpc_id": lb.get("VpcId"),
                    "sgs": lb.get("SecurityGroups", []), "state": lb["State"]["Code"]})
    return out


def describe_alb(region: str, alb_arn: str) -> dict:
    """拿 ALB 的 VPC / SG / AZ / scheme(BYO 时用来接 TG、配 SG、校验)。"""
    lb = client("elbv2", region).describe_load_balancers(LoadBalancerArns=[alb_arn])["LoadBalancers"][0]
    return {"alb_arn": alb_arn, "alb_dns": lb["DNSName"], "vpc_id": lb["VpcId"], "scheme": lb["Scheme"],
            "sgs": lb.get("SecurityGroups", []), "state": lb["State"]["Code"],
            "azs": [z["ZoneName"] for z in lb.get("AvailabilityZones", [])]}


def listener_ports(region: str, alb_arn: str) -> dict[int, str]:
    """ALB 现有 listener 的 {端口: 协议}(BYO 时判断是否已有 443/HTTPS,决定 GA 是否需要 PortOverride)。"""
    r = client("elbv2", region).describe_listeners(LoadBalancerArn=alb_arn)
    return {l["Port"]: l.get("Protocol", "") for l in r.get("Listeners", [])}


def create_target_group(region: str, vpc_id: str, serving_port: int, health_path: str,
                        name: str | None = None, dry_run: bool = True) -> dict:
    """建 TG(HTTP/serving_port,/health,LOR)。幂等复用同名。BYO 与 auto 共用。"""
    tg_name = (name or f"nlp-tg-{vpc_id[-6:]}")[:32]
    if dry_run:
        return {"planned": f"create TG {tg_name} (instance,{serving_port},{health_path})", "tg_arn": "tg-DRYRUN"}
    elb = client("elbv2", region)
    try:
        tg = elb.create_target_group(Name=tg_name, Protocol="HTTP", Port=serving_port, VpcId=vpc_id,
                                     TargetType="instance", HealthCheckPath=health_path,
                                     HealthCheckIntervalSeconds=15, HealthyThresholdCount=2,
                                     UnhealthyThresholdCount=2)["TargetGroups"][0]
    except Exception as e:  # noqa: BLE001
        if "DuplicateTargetGroupName" not in str(e):
            raise
        tg = elb.describe_target_groups(Names=[tg_name])["TargetGroups"][0]
    elb.modify_target_group_attributes(TargetGroupArn=tg["TargetGroupArn"], Attributes=[
        {"Key": "load_balancing.algorithm.type", "Value": "least_outstanding_requests"},
        {"Key": "deregistration_delay.timeout_seconds", "Value": "60"}])
    return {"tg_arn": tg["TargetGroupArn"], "tg_name": tg_name}


def ensure_listener_forward(region: str, alb_arn: str, port: int, tg_arn: str, dry_run: bool = True) -> dict:
    """确保客户 ALB 在 port 上有 listener 转发到 tg_arn。无→建;已转发到本 TG→ok;转发到别的 TG→冲突(只报不覆盖)。"""
    if dry_run:
        return {"planned": f"ensure listener :{port} -> {tg_arn}"}
    elb = client("elbv2", region)
    listeners = elb.describe_listeners(LoadBalancerArn=alb_arn).get("Listeners", [])
    existing = next((l for l in listeners if l.get("Port") == port), None)
    if not existing:
        elb.create_listener(LoadBalancerArn=alb_arn, Protocol="HTTP", Port=port,
                            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}])
        return {"status": "fixed", "listener": "created", "port": port}
    fwd: list[str] = []
    for a in existing.get("DefaultActions", []):
        if a.get("Type") == "forward":
            if a.get("TargetGroupArn"):
                fwd.append(a["TargetGroupArn"])
            fwd += [t["TargetGroupArn"] for t in a.get("ForwardConfig", {}).get("TargetGroups", [])]
    if tg_arn in fwd:
        return {"status": "ok", "listener": "already-forwarding", "port": port}
    return {"status": "warn", "listener": "conflict", "port": port, "forwards_to": fwd,
            "detail": f"ALB :{port} 已有 listener 转发到其它 TG,未覆盖,请手动确认或改用专用 ALB"}
