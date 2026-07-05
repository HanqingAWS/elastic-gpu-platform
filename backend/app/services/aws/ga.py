"""Global Accelerator:把区域 ALB 注册进对应 endpoint group(开启 client IP preservation,
以触发 GA 在该数据平面 VPC 自动创建名为 GlobalAccelerator 的 SG),再引用该 SG 放行入站。

顺序依赖(关键):先 update-endpoint-group 注册 ALB(ClientIPPreservationEnabled=True)
→ 轮询该 VPC 出现 GlobalAccelerator SG → authorize-security-group-ingress 引用它。"""
from __future__ import annotations
import time
from .session import client


def _ga():
    return client("globalaccelerator", "us-west-2")  # GA 全局服务,控制端点在 us-west-2


def resolve_accelerator_arn(configured: str | None) -> str | None:
    if configured:
        return configured
    try:
        accs = _ga().list_accelerators().get("Accelerators", [])
        for a in accs:
            if "nlp" in a.get("Name", "").lower():
                return a["AcceleratorArn"]
        return accs[0]["AcceleratorArn"] if accs else None
    except Exception:  # noqa: BLE001
        return None


def describe_topology(configured_arn: str | None) -> dict:
    """返回 GA 完整拓扑供 UI:DNS/静态 IP/状态 + 每监听器每区 endpoint group(TrafficDial/健康/endpoints)。"""
    arn = resolve_accelerator_arn(configured_arn)
    if not arn:
        return {"configured": False, "accelerator": None, "listeners": []}
    ga = _ga()
    acc = ga.describe_accelerator(AcceleratorArn=arn)["Accelerator"]
    static_ips: list[str] = []
    for s in acc.get("IpSets", []):
        static_ips += s.get("IpAddresses", [])
    out_listeners = []
    for lst in ga.list_listeners(AcceleratorArn=arn).get("Listeners", []):
        egs = []
        for eg in ga.list_endpoint_groups(ListenerArn=lst["ListenerArn"]).get("EndpointGroups", []):
            egs.append({
                "region": eg["EndpointGroupRegion"],
                "traffic_dial": eg.get("TrafficDialPercentage", 100),
                "health_check_port": eg.get("HealthCheckPort"),
                "health_check_path": eg.get("HealthCheckPath"),
                "endpoints": [{
                    "endpoint_id": e.get("EndpointId"),
                    "weight": e.get("Weight"),
                    "health_state": e.get("HealthState"),
                    "client_ip_preservation": e.get("ClientIPPreservationEnabled"),
                } for e in eg.get("EndpointDescriptions", [])],
            })
        out_listeners.append({
            "listener_arn": lst["ListenerArn"],
            "protocol": lst.get("Protocol"),
            "port_ranges": lst.get("PortRanges", []),
            "endpoint_groups": egs,
        })
    return {
        "configured": True,
        "accelerator": {
            "arn": arn,
            "name": acc.get("Name"),
            "status": acc.get("Status"),
            "enabled": acc.get("Enabled"),
            "dns_name": acc.get("DnsName"),
            "ip_type": acc.get("IpAddressType"),
            "static_ips": static_ips,
        },
        "listeners": out_listeners,
    }


def find_endpoint_group_arn(accelerator_arn: str, region: str) -> str | None:
    ga = client("globalaccelerator", "us-west-2")  # GA 是全局服务,API 端点在 us-west-2
    for lp in ga.get_paginator("list_listeners").paginate(AcceleratorArn=accelerator_arn):  # 分页,避免截断漏掉该区
        for lst in lp["Listeners"]:
            for ep in ga.get_paginator("list_endpoint_groups").paginate(ListenerArn=lst["ListenerArn"]):
                for eg in ep["EndpointGroups"]:
                    if eg["EndpointGroupRegion"] == region:
                        return eg["EndpointGroupArn"]
    return None


def register_alb(accelerator_arn: str, region: str, alb_arn: str,
                 listener_port: int = 443, endpoint_port: int = 80, dry_run: bool = True) -> dict:
    """把 ALB 注册进该区 endpoint group。GA 监听 443,ALB 监听 80 → 用 PortOverrides 把 443 映射到 80。"""
    if dry_run:
        eg_arn = None
        try:
            eg_arn = find_endpoint_group_arn(accelerator_arn, region)
        except Exception:  # noqa: BLE001  离线/无凭证时 dry-run 仍可继续
            pass
        return {"planned": f"register ALB into GA endpoint group ({region}) ClientIPPreservation=True, "
                           f"portOverride {listener_port}->{endpoint_port}", "endpoint_group_arn": eg_arn}
    eg_arn = find_endpoint_group_arn(accelerator_arn, region)
    if not eg_arn:
        raise RuntimeError(f"未找到 {region} 的 GA endpoint group(检查 GA 栈是否覆盖该区)")
    ga = client("globalaccelerator", "us-west-2")
    ga.update_endpoint_group(
        EndpointGroupArn=eg_arn,
        EndpointConfigurations=[{"EndpointId": alb_arn, "Weight": 100, "ClientIPPreservationEnabled": True}],
        PortOverrides=[{"ListenerPort": listener_port, "EndpointPort": endpoint_port}],
    )
    return {"endpoint_group_arn": eg_arn, "port_override": f"{listener_port}->{endpoint_port}"}


def wait_global_accelerator_sg(region: str, vpc_id: str, timeout_s: int = 180) -> str | None:
    """轮询该 VPC 内 GA 自动创建的名为 GlobalAccelerator 的 SG。"""
    ec2 = client("ec2", region)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = ec2.describe_security_groups(Filters=[
            {"Name": "group-name", "Values": ["GlobalAccelerator"]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ])
        if r["SecurityGroups"]:
            return r["SecurityGroups"][0]["GroupId"]
        time.sleep(10)
    return None


def authorize_ga_ingress(region: str, node_sg_id: str, ga_sg_id: str, ports: list[int], dry_run: bool = True) -> dict:
    """在节点/ALB SG 里引用 GA 的 GlobalAccelerator SG 作入站来源(非 CIDR、非 0.0.0.0/0)。"""
    if dry_run:
        return {"planned": f"authorize {node_sg_id} ingress from {ga_sg_id} on {ports} (UserIdGroupPairs)"}
    ec2 = client("ec2", region)
    added, existed = [], []
    for p in ports:  # 逐端口授权,已存在则跳过(幂等,支持重试)
        try:
            ec2.authorize_security_group_ingress(GroupId=node_sg_id, IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": p, "ToPort": p,
                "UserIdGroupPairs": [{"GroupId": ga_sg_id, "Description": "from GlobalAccelerator SG"}]}])
            added.append(p)
        except Exception as e:  # noqa: BLE001
            if "InvalidPermission.Duplicate" not in str(e):
                raise
            existed.append(p)
    return {"authorized": added, "already_present": existed, "from_sg": ga_sg_id}
