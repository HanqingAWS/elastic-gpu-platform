"""数据平面 EC2 资源:VPC/子网(发现或新建)、SG(引用 GA 自动创建的 GlobalAccelerator SG,
绝不 0.0.0.0/0)、密钥对、Launch Template(p4de/p4d)。所有写操作支持 dry_run。"""
from __future__ import annotations
import base64
from .session import client

TAG = [{"Key": "managed-by", "Value": "nlp-platform"}]


# ---------- 发现 ----------
def list_vpcs(region: str) -> list[dict]:
    r = client("ec2", region).describe_vpcs()
    return [{"vpc_id": v["VpcId"], "cidr": v["CidrBlock"], "is_default": v.get("IsDefault", False)} for v in r["Vpcs"]]


def list_subnets(region: str, vpc_id: str) -> list[dict]:
    r = client("ec2", region).describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    return [{"subnet_id": s["SubnetId"], "az": s["AvailabilityZone"], "cidr": s["CidrBlock"]} for s in r["Subnets"]]


def _sg_open_to_world(g: dict) -> bool:
    """SG 是否含 0.0.0.0/0 或 ::/0 入站(平台硬约束:禁止)。"""
    for p in g.get("IpPermissions", []):
        if any(x.get("CidrIp") == "0.0.0.0/0" for x in p.get("IpRanges", [])):
            return True
        if any(x.get("CidrIpv6") == "::/0" for x in p.get("Ipv6Ranges", [])):
            return True
    return False


def list_security_groups(region: str, vpc_id: str) -> list[dict]:
    r = client("ec2", region).describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    return [{"sg_id": g["GroupId"], "name": g.get("GroupName"), "desc": g.get("Description", ""),
             "open_to_world": _sg_open_to_world(g)} for g in r["SecurityGroups"]]


def sg_is_open(region: str, sg_id: str) -> bool:
    r = client("ec2", region).describe_security_groups(GroupIds=[sg_id])
    return bool(r["SecurityGroups"]) and _sg_open_to_world(r["SecurityGroups"][0])


def list_key_pairs(region: str) -> list[dict]:
    r = client("ec2", region).describe_key_pairs()
    return [{"key_name": k["KeyName"]} for k in r.get("KeyPairs", [])]


def one_subnet_per_az(region: str, subnet_ids: list[str]) -> list[str]:
    """ALB 每个 AZ 只能挂 1 个子网:按 AZ 去重(每 AZ 取第一个),保序。拿不到时原样返回。"""
    if not subnet_ids:
        return subnet_ids
    try:
        r = client("ec2", region).describe_subnets(SubnetIds=subnet_ids)
        az_of = {s["SubnetId"]: s["AvailabilityZone"] for s in r["Subnets"]}
        seen, chosen = set(), []
        for sid in subnet_ids:  # 保持用户选择顺序
            az = az_of.get(sid)
            if az and az not in seen:
                seen.add(az)
                chosen.append(sid)
        return chosen or subnet_ids
    except Exception:  # noqa: BLE001
        return subnet_ids


def offered_instance_types(region: str, candidates: list[str]) -> list[str]:
    """过滤出该区实际提供的机型(保序)。用于容错:该区没有 p4de 就自动只用 p4d。
    拿不到时返回原列表(交给后续 AWS 校验),不误伤。"""
    try:
        r = client("ec2", region).describe_instance_type_offerings(
            LocationType="region", Filters=[{"Name": "instance-type", "Values": candidates}])
        avail = {o["InstanceType"] for o in r.get("InstanceTypeOfferings", [])}
        return [t for t in candidates if t in avail]
    except Exception:  # noqa: BLE001
        return list(candidates)


def latest_spot_price(region: str, instance_type: str = "p4de.24xlarge") -> float | None:
    """最新 Spot 价(取各 AZ 最低,近似 capacity-optimized 会落到的价);拿不到返回 None。"""
    try:
        r = client("ec2", region).describe_spot_price_history(
            InstanceTypes=[instance_type], ProductDescriptions=["Linux/UNIX"], MaxResults=20)
        prices = [float(p["SpotPrice"]) for p in r.get("SpotPriceHistory", [])]
        return round(min(prices), 4) if prices else None
    except Exception:  # noqa: BLE001
        return None


# ---------- 创建 VPC/子网(客户无默认 VPC 时) ----------
def create_vpc(region: str, cidr: str = "10.30.0.0/16", dry_run: bool = True) -> dict:
    """幂等新建/复用受管 VPC(Name=nlp-vpc-{region})。重试时复用已有 VPC/IGW/路由表/子网,
    不再每次泄漏一整套。子网只落在 state=available 的标准 AZ。"""
    if dry_run:
        return {"planned": f"create/reuse VPC {cidr} @ {region} + IGW + public subnets (每 AZ 一个)"}
    ec2 = client("ec2", region)
    name = f"nlp-vpc-{region}"
    tags = TAG + [{"Key": "Name", "Value": name}]
    ts = lambda rt: [{"ResourceType": rt, "Tags": tags}]  # noqa: E731

    ex = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [name]},
                                    {"Name": "tag:managed-by", "Values": ["nlp-platform"]}]).get("Vpcs", [])
    if ex:
        vpc_id = ex[0]["VpcId"]
    else:
        vpc_id = ec2.create_vpc(CidrBlock=cidr, TagSpecifications=ts("vpc"))["Vpc"]["VpcId"]
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    # IGW:查回或建
    igws = ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]).get("InternetGateways", [])
    if igws:
        igw = igws[0]["InternetGatewayId"]
    else:
        igw = ec2.create_internet_gateway(TagSpecifications=ts("internet-gateway"))["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc_id)
    # 路由表(受管 tag 查回或建)+ 默认路由
    rts = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                                             {"Name": "tag:managed-by", "Values": ["nlp-platform"]}]).get("RouteTables", [])
    rt = rts[0]["RouteTableId"] if rts else ec2.create_route_table(VpcId=vpc_id, TagSpecifications=ts("route-table"))["RouteTable"]["RouteTableId"]
    try:
        ec2.create_route(RouteTableId=rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)  # 路由,非 SG,允许
    except Exception as e:  # noqa: BLE001
        if "RouteAlreadyExists" not in str(e):
            raise
    # 子网:每可用 AZ 一个(已存在受管子网则复用)
    existing = {s["AvailabilityZone"]: s["SubnetId"] for s in ec2.describe_subnets(Filters=[
        {"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "tag:managed-by", "Values": ["nlp-platform"]}]).get("Subnets", [])}
    azs = [z["ZoneName"] for z in ec2.describe_availability_zones(Filters=[
        {"Name": "state", "Values": ["available"]},
        {"Name": "zone-type", "Values": ["availability-zone"]}])["AvailabilityZones"][:3]]
    subnets = []
    for i, az in enumerate(azs):
        if az in existing:
            subnets.append({"subnet_id": existing[az], "az": az})
            continue
        try:
            sn = ec2.create_subnet(VpcId=vpc_id, CidrBlock=f"10.30.{i}.0/24", AvailabilityZone=az,
                                   TagSpecifications=ts("subnet"))["Subnet"]["SubnetId"]
        except Exception as e:  # noqa: BLE001
            if "InvalidSubnet.Conflict" not in str(e):
                raise
            continue  # 该 CIDR 已占用 → 跳过(用其余 AZ)
        ec2.modify_subnet_attribute(SubnetId=sn, MapPublicIpOnLaunch={"Value": True})
        try:
            ec2.associate_route_table(RouteTableId=rt, SubnetId=sn)
        except Exception as e:  # noqa: BLE001
            if "Resource.AlreadyAssociated" not in str(e):
                raise
        subnets.append({"subnet_id": sn, "az": az})
    return {"vpc_id": vpc_id, "subnets": subnets, "reused": bool(ex)}


# ---------- 安全组(核心:绝不 0.0.0.0/0) ----------
def create_node_sg(region: str, vpc_id: str, serving_port: int, metrics_port: int,
                   control_plane_egress_cidr: str, dry_run: bool = True) -> dict:
    """创建节点/ALB 用 SG。初始不放 GA 入站(等 ALB 注册进 GA、GlobalAccelerator SG 出现后再加,见 ga.py)。
    metrics 端口只放行控制平面出口 /32(跨 VPC/区无法用 SG 引用,故用 NAT EIP /32,仍非 0.0.0.0/0)。"""
    if dry_run:
        return {"planned": f"create SG @ {vpc_id}: 入站仅 metrics:{metrics_port}<-{control_plane_egress_cidr};"
                           f" GA 入站待 GlobalAccelerator SG 出现后加 (443/{serving_port})"}
    ec2 = client("ec2", region)
    name = f"nlp-node-{vpc_id[-6:]}"
    try:  # 幂等:重试时 SG 已存在则复用(按名+VPC 查回)
        sg = ec2.create_security_group(GroupName=name, Description="nlp-platform node",
                                       VpcId=vpc_id, TagSpecifications=[{"ResourceType": "security-group", "Tags": TAG}])["GroupId"]
    except Exception as e:  # noqa: BLE001
        if "InvalidGroup.Duplicate" not in str(e):
            raise
        r = ec2.describe_security_groups(Filters=[
            {"Name": "group-name", "Values": [name]}, {"Name": "vpc-id", "Values": [vpc_id]}])
        if not r["SecurityGroups"]:
            raise RuntimeError(f"SG {name} 报重复但按名+VPC 查不到,无法复用")
        g = r["SecurityGroups"][0]
        if _sg_open_to_world(g):  # 复用路径也守住硬约束:禁止 0.0.0.0/0
            raise RuntimeError(f"复用的安全组 {g['GroupId']} 含 0.0.0.0/0 入站,违反平台硬约束")
        sg = g["GroupId"]
    # metrics:只放控制平面出口 /32(严禁 0.0.0.0/0)。规则已存在则忽略(幂等)
    try:
        ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": metrics_port, "ToPort": metrics_port,
            "IpRanges": [{"CidrIp": control_plane_egress_cidr, "Description": "control-plane metrics scrape"}],
        }])
    except Exception as e:  # noqa: BLE001
        if "InvalidPermission.Duplicate" not in str(e):
            raise
    return {"sg_id": sg}


def authorize_metrics_ingress(region: str, sg_id: str, metrics_port: int, cidr: str, dry_run: bool = True) -> dict:
    """给(复用的)现有 SG 补 metrics 抓取入站:tcp/metrics_port <- 控制面出口 /32。幂等。
    自动创建 SG 时 create_node_sg 已加此规则;复用现有 SG 走此函数补齐,使监控页能抓到每台 /metrics。"""
    if dry_run:
        return {"planned": f"metrics ingress {sg_id} tcp/{metrics_port} <- {cidr}"}
    ec2 = client("ec2", region)
    try:
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": metrics_port, "ToPort": metrics_port,
            "IpRanges": [{"CidrIp": cidr, "Description": "control-plane metrics scrape"}]}])
        return {"metrics_ingress": metrics_port, "from": cidr}
    except Exception as e:  # noqa: BLE001
        if "InvalidPermission.Duplicate" not in str(e):
            raise
        return {"metrics_ingress": metrics_port, "from": cidr, "already_present": True}


def authorize_self_ingress(region: str, sg_id: str, port: int, dry_run: bool = True) -> dict:
    """ALB 与节点同处一个 SG → 需自引用规则允许 ALB 访问目标端口(转发 + 健康检查)。幂等。"""
    if dry_run:
        return {"planned": f"self-ingress {sg_id} tcp/{port} (ALB->target)"}
    ec2 = client("ec2", region)
    try:
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
            "UserIdGroupPairs": [{"GroupId": sg_id, "Description": "ALB to target (self-ref)"}]}])
        return {"self_authorized": port}
    except Exception as e:  # noqa: BLE001
        if "InvalidPermission.Duplicate" not in str(e):
            raise
        return {"self_authorized": port, "already_present": True}


def create_key_pair(region: str, name: str, dry_run: bool = True) -> dict:
    if dry_run:
        return {"planned": f"create key pair {name} @ {region}"}
    ec2 = client("ec2", region)
    try:
        ec2.delete_key_pair(KeyName=name)
    except Exception:  # noqa: BLE001
        pass
    r = ec2.create_key_pair(KeyName=name, TagSpecifications=[{"ResourceType": "key-pair", "Tags": TAG}])
    return {"key_name": name, "key_material_present": bool(r.get("KeyMaterial"))}  # 私钥应存 Secrets Manager


def _user_data() -> str:
    # AMI 自带模型+引擎并开机自服务;此处仅打标记。真实自适应 TP 逻辑在客户 AMI 内。
    return base64.b64encode(b"#!/bin/bash\necho nlp-platform node boot\n").decode()


def create_launch_template(region: str, name: str, ami_id: str, instance_type: str, sg_id: str,
                           key_name: str | None, instance_profile_arn: str, dry_run: bool = True) -> dict:
    if dry_run:
        return {"planned": f"create LT {name}: {instance_type} ami={ami_id} sg={sg_id} key={key_name or '(none)'}"}
    ec2 = client("ec2", region)
    lt_data = {
        "ImageId": ami_id, "InstanceType": instance_type,
        "SecurityGroupIds": [sg_id], "UserData": _user_data(),
        "IamInstanceProfile": {"Arn": instance_profile_arn},
        "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 300, "VolumeType": "gp3", "DeleteOnTermination": True}}],
        "TagSpecifications": [{"ResourceType": "instance", "Tags": TAG}],
    }
    if key_name:  # 留空 = 不注入密钥
        lt_data["KeyName"] = key_name
    try:
        r = ec2.create_launch_template(LaunchTemplateName=name, LaunchTemplateData=lt_data,
                                       TagSpecifications=[{"ResourceType": "launch-template", "Tags": TAG}])
        return {"lt_id": r["LaunchTemplate"]["LaunchTemplateId"]}
    except Exception as e:  # noqa: BLE001  幂等:同名 LT 已存在 → 追加新版本($Latest 自动生效,复用的 ASG 也会用上新 AMI)
        if "InvalidLaunchTemplateName.AlreadyExistsException" not in str(e):
            raise
        lt = ec2.describe_launch_templates(LaunchTemplateNames=[name])["LaunchTemplates"][0]
        lt_id = lt["LaunchTemplateId"]
        ec2.create_launch_template_version(LaunchTemplateId=lt_id, LaunchTemplateData=lt_data,
                                           SourceVersion=str(lt["LatestVersionNumber"]))
        return {"lt_id": lt_id, "note": "appended new version (reused LT)"}
