"""编排单区数据平面 provisioning(dry_run 优先)。顺序见实施方案:
VPC/子网(选或建)→ 节点 SG(metrics 放控制平面 /32)→ 密钥 → LT → ALB+TG →
注册 GA endpoint(client IP preservation)→ 轮询 GlobalAccelerator SG → 引用它放行 GA 入站
→ GPU ASG(100% 按需,机型优先级可配置,desired=0)。全程绝不 0.0.0.0/0。
注:需求变更(2026-07-04)—— GPU 全按需(Spot 抢不到 p4d/p4de),不再创建 Spot ASG;
存量 nlp-spot-* 保留但永久归零(便于回退)。机型优先级读 Config.instance_type_priority。"""
from __future__ import annotations
from ..core.config import get_settings
from ..db.dynamo import get_dynamo
from .aws import ec2, elbv2, ga, autoscaling
from .aws.session import client

ALB_LISTENER_PORT = 80  # ALB HTTP 监听端口;GA(443)经 PortOverride 转发到此端口
DEFAULT_TYPES = ["p4d.24xlarge", "p4de.24xlarge"]  # 兜底默认:p4d 优先


def configured_types(region: str | None = None) -> list[str]:
    """机型优先级(顺序即优先级):按区覆盖(Config.regions[region].instance_types)
    → 全局 instance_type_priority → 默认。空/异常回落默认。"""
    try:
        cfg = get_dynamo().get_config()
        if region:
            rc = (cfg.get("regions") or {}).get(region) or {}
            per = [str(x).strip() for x in (rc.get("instance_types") or []) if str(x).strip()]
            if per:
                return per
        t = [str(x).strip() for x in (cfg.get("instance_type_priority") or []) if str(x).strip()]
        return t or list(DEFAULT_TYPES)
    except Exception:  # noqa: BLE001
        return list(DEFAULT_TYPES)


def region_status(region: str, vpc_id: str | None = None) -> dict:
    """真实检测该区数据面是否已建成 —— 以 GPU(按需)ASG 是否存在为准。"""
    od_name = f"nlp-od-{region}"
    asgs = autoscaling.get_asgs(region, [od_name, f"nlp-spot-{region}"])
    od = asgs.get(od_name)
    alb = None
    if vpc_id:
        try:
            name = f"nlp-{region}-{vpc_id[-6:]}"[:32]
            lb = elbv2.client("elbv2", region).describe_load_balancers(Names=[name])["LoadBalancers"][0]
            alb = {"exists": True, "dns": lb["DNSName"], "state": lb["State"]["Code"]}
        except Exception:  # noqa: BLE001
            alb = {"exists": False}
    state = "created" if od else "none"
    return {"region": region, "state": state, "provisioned": bool(od),
            "od_asg": od, "spot_asg": asgs.get(f"nlp-spot-{region}"), "alb": alb}


def deprovision_region(region: str, vpc_id: str | None = None, progress=None) -> dict:
    """硬删除该区数据面 AWS 资源:GA endpoint group / ALB(+listener) / TargetGroup / ASG(od+spot) / LT。
    **保留 VPC / 子网 / IGW / 路由表 / 安全组**(免费、可复用,且避开 GA 托管 ENI 异步回收的拆 VPC 之痛)。
    best-effort:每步独立 try/except,不因单步失败中断。vpc_id 传入则按 VPC 精确过滤 ALB/TG(避免误删同区孤儿栈)。"""
    s = get_settings()
    steps: list[dict] = []

    def emit(x: dict):
        steps.append(x)
        if progress:
            progress(x)

    # 1) ASG(od + spot)强制删除(desired 归零 + 删组)
    asc = client("autoscaling", region)
    for name in (f"nlp-od-{region}", f"nlp-spot-{region}"):
        try:
            asc.delete_auto_scaling_group(AutoScalingGroupName=name, ForceDelete=True)
            emit({"asg_deleted": name})
        except Exception as e:  # noqa: BLE001
            if "not found" not in str(e).lower():
                emit({"asg_skip": f"{name}: {e}"})
    # 2) Launch Template
    ec2c = client("ec2", region)
    try:
        for lt in ec2c.describe_launch_templates(
                Filters=[{"Name": "launch-template-name", "Values": [f"nlp-lt-{region}"]}]).get("LaunchTemplates", []):
            ec2c.delete_launch_template(LaunchTemplateId=lt["LaunchTemplateId"])
            emit({"lt_deleted": lt["LaunchTemplateName"]})
    except Exception as e:  # noqa: BLE001
        emit({"lt_skip": str(e)})
    # 3) GA endpoint group(连带把 ALB 从 GA 摘除)
    try:
        eg = ga.find_endpoint_group_arn(s.ga_accelerator_arn, region) if s.ga_accelerator_arn else None
        if eg:
            client("globalaccelerator", "us-west-2").delete_endpoint_group(EndpointGroupArn=eg)
            emit({"ga_endpoint_group_deleted": region})
    except Exception as e:  # noqa: BLE001
        emit({"ga_skip": str(e)})
    # 4) ALB(按区名前缀 + VPC 精确过滤,避免误删同区孤儿栈)→ 等删除完成 → 删其孤立 TG
    elb = client("elbv2", region)
    try:
        lbs = [lb for lb in elb.describe_load_balancers().get("LoadBalancers", [])
               if lb["LoadBalancerName"].startswith(f"nlp-{region}-") and (not vpc_id or lb.get("VpcId") == vpc_id)]
        for lb in lbs:
            elb.delete_load_balancer(LoadBalancerArn=lb["LoadBalancerArn"])
            emit({"alb_deleting": lb["LoadBalancerName"]})
        if lbs:
            try:
                elb.get_waiter("load_balancers_deleted").wait(
                    LoadBalancerArns=[lb["LoadBalancerArn"] for lb in lbs],
                    WaiterConfig={"Delay": 15, "MaxAttempts": 20})
            except Exception:  # noqa: BLE001
                pass
        for tg in elb.describe_target_groups().get("TargetGroups", []):
            if tg["TargetGroupName"].startswith("nlp-tg-") and (not vpc_id or tg.get("VpcId") == vpc_id) \
                    and not tg.get("LoadBalancerArns"):
                try:
                    elb.delete_target_group(TargetGroupArn=tg["TargetGroupArn"])
                    emit({"tg_deleted": tg["TargetGroupName"]})
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        emit({"alb_skip": str(e)})
    emit({"kept": "VPC / 子网 / IGW / 路由表 / 安全组 保留(可复用;GA 托管 ENI 由 GA 异步回收)"})
    return {"region": region, "steps": steps}


def _provision_byo(region, ami_id, *, vpc_id, asg_subnet_ids, alb_arn, ga_accelerator_arn,
                   sg_id, key_name, serving_port, health_path, metrics_port, dry_run, emit, steps, s):
    """BYO 子流程:现有公网 ALB + 选定 GA + ASG 放私有子网。步骤经 emit 上报,复用 provision_region 的 steps。"""
    ga_arn = ga_accelerator_arn or s.ga_accelerator_arn or "arn:aws:globalaccelerator::DRYRUN"
    if not alb_arn or not asg_subnet_ids:
        raise RuntimeError("BYO 模式需提供 alb_arn(现有公网 ALB)和 asg_subnet_ids(私有子网)")

    # 0) 读现有 ALB:拿 VPC / SG / scheme(ASG/TG/SG 都据此)
    if dry_run:
        info = {"vpc_id": vpc_id or "vpc-DRYRUN", "sgs": [], "scheme": "internet-facing"}
    else:
        info = elbv2.describe_alb(region, alb_arn)
    vpc_id = info["vpc_id"]
    alb_sgs = info.get("sgs", [])
    if not dry_run and info.get("scheme") != "internet-facing":
        raise RuntimeError(f"所选 ALB 不是公网(internet-facing),scheme={info.get('scheme')}。请改用公网 ALB。")
    emit({"alb_selected": {"alb_arn": alb_arn, "vpc_id": vpc_id, "scheme": info.get("scheme"), "sgs": alb_sgs}})

    # 1) 节点 SG:建或用现有;放行 metrics + 来自 ALB SG 的 serving_port(ALB→实例)
    if sg_id:
        emit({"sg": {"use-existing": sg_id}})
        emit({"sg_metrics": ec2.authorize_metrics_ingress(region, sg_id, metrics_port, s.control_plane_egress_cidr, dry_run=dry_run)})
    else:
        sg = ec2.create_node_sg(region, vpc_id, serving_port, metrics_port, s.control_plane_egress_cidr, dry_run=dry_run)
        emit({"sg": sg})
        sg_id = sg.get("sg_id", "sg-DRYRUN")
    for asg_sg in alb_sgs:
        emit({"alb_to_node_ingress": ec2.authorize_ingress_from_sg(region, sg_id, asg_sg, serving_port, "from ALB SG", dry_run=dry_run)})

    # 2) 密钥
    emit({"key": {"use-existing": key_name} if key_name else {"none": "不注入密钥(实例无 SSH key;用 SSM 登录)"}})

    # 3) 机型 + Launch Template
    want_types = configured_types(region)
    valid_types = ec2.offered_instance_types(region, want_types) if not dry_run else want_types
    skipped = [t for t in want_types if t not in valid_types]
    if not valid_types:
        raise RuntimeError(f"{region} 不提供任何目标机型 {want_types},无法创建 GPU ASG。")
    emit({"instance_types": {"used": valid_types, "skipped": skipped, "source": "config.instance_type_priority"}})
    lt = ec2.create_launch_template(region, f"nlp-lt-{region}", ami_id, valid_types[0], sg_id, key_name,
                                    s.gpu_node_instance_profile_arn or "arn:aws:iam::DRYRUN:instance-profile/x", dry_run=dry_run)
    emit({"launch_template": lt})
    lt_id = lt.get("lt_id", "lt-DRYRUN")

    # 4) TG(挂在客户 ALB)+ 确保 80 listener 转发到它(缺则建;冲突只报不覆盖)
    tg = elbv2.create_target_group(region, vpc_id, serving_port, health_path, dry_run=dry_run)
    emit({"target_group": tg})
    tg_arn = tg.get("tg_arn", "tg-DRYRUN")
    emit({"listener": elbv2.ensure_listener_forward(region, alb_arn, ALB_LISTENER_PORT, tg_arn, dry_run=dry_run)})

    # 5) 注册进【所选 GA】的该区 endpoint group(缺则建),再把 GA SG 放行进【ALB 的 SG】
    #    端口自适应(见 ga.register_alb):BYO ALB 已有 443 listener(带证书)→ 免 PortOverride 直达 443;
    #    此时 GA 入站放行与健康检查端口都随实际转发端口(reg.endpoint_port)。
    reg = ga.register_alb(ga_arn, region, alb_arn, listener_port=443, endpoint_port=ALB_LISTENER_PORT, dry_run=dry_run)
    emit({"ga_register": reg})
    ga_traffic_port = reg.get("endpoint_port", ALB_LISTENER_PORT)
    if reg.get("alb_direct"):
        emit({"ga_note": {"alb_443_direct": "ALB 已有 443 listener:GA 443 直达 ALB 443(未写 PortOverride,历史残留已清除)。"
                                            "请确认该 443 listener 的规则会转发到平台 TG,否则 GA 流量不会到达 GPU 节点"}})
    if dry_run:
        emit({"ga_ingress": {"planned": "wait GlobalAccelerator SG then authorize on ALB SG :%d" % ga_traffic_port}})
    else:
        ga_sg = ga.wait_global_accelerator_sg(region, vpc_id)
        if not ga_sg:
            emit({"ga_ingress": {"error": "GlobalAccelerator SG 未在超时内出现"}})
        else:
            for asg_sg in (alb_sgs or [sg_id]):
                emit({"ga_ingress": ec2.authorize_ingress_from_sg(region, asg_sg, ga_sg, ga_traffic_port, "from GlobalAccelerator SG", dry_run=False)})

    # 6) GPU ASG(100% 按需,desired=0)放【私有子网】,挂到 TG
    od = autoscaling.create_od_asg(region, f"nlp-od-{region}", lt_id, asg_subnet_ids, tg_arn, types=valid_types, dry_run=dry_run)
    if od.get("note") == "already exists (reused)":
        od = {**od, "types_update": autoscaling.update_asg_types(region, f"nlp-od-{region}", lt_id, valid_types, dry_run=dry_run)}
    emit({"gpu_asg": od})

    # 7) 末尾跑校验(自动补 SG,其余风险点告警)
    if not dry_run:
        emit({"validation": validate_region(region, alb_arn, ga_arn, asg_subnet_ids=asg_subnet_ids, node_sg_id=sg_id, serving_port=serving_port)})
    return {"region": region, "mode": "byo", "dry_run": dry_run, "vpc_id": vpc_id, "steps": steps}


def validate_region(region: str, alb_arn: str, ga_accelerator_arn: str | None = None,
                    asg_subnet_ids: list[str] | None = None, node_sg_id: str | None = None,
                    serving_port: int = 8000, autofix: bool = True) -> list[dict]:
    """BYO 校验 + 分级处理。安全幂等的自动补(GA SG→ALB SG、缺 listener、ALB SG→节点 SG);
    其余风险点只报(warn/fail)。每项 {name, status: ok|fixed|warn|fail, detail}。"""
    s = get_settings()
    ga_arn = ga_accelerator_arn or s.ga_accelerator_arn
    out: list[dict] = []

    def add(name, status, detail=""):
        out.append({"name": name, "status": status, "detail": detail})

    # 1) ALB internet-facing
    try:
        info = elbv2.describe_alb(region, alb_arn)
    except Exception as e:  # noqa: BLE001
        add("alb", "fail", f"无法读取 ALB:{e}")
        return out
    vpc_id, alb_sgs = info["vpc_id"], info.get("sgs", [])
    if info.get("scheme") != "internet-facing":
        add("alb_scheme", "fail", f"ALB scheme={info.get('scheme')},需 internet-facing(公网),请改用公网 ALB")
    else:
        add("alb_scheme", "ok", f"internet-facing @ {vpc_id}")

    # 2) GA 该区 endpoint group 是否注册了该 ALB
    try:
        eg_arn = ga.find_endpoint_group_arn(ga_arn, region) if ga_arn else None
        if not eg_arn:
            add("ga_endpoint_group", "warn", f"所选 GA 该区无 endpoint group;provision 会自动建并注册")
        else:
            gcli = ga._ga()
            eg = gcli.describe_endpoint_group(EndpointGroupArn=eg_arn)["EndpointGroup"]
            ids = [e.get("EndpointId") for e in eg.get("EndpointDescriptions", [])]
            if alb_arn in ids:
                hs = [e.get("HealthState") for e in eg.get("EndpointDescriptions", []) if e.get("EndpointId") == alb_arn]
                add("ga_registered", "ok", f"ALB 已注册进 GA endpoint group,health={hs}")
            else:
                add("ga_registered", "warn", "ALB 未注册进 GA endpoint group(provision 会注册)")
            # 2b) PortOverride 与 ALB listener 是否错配:ALB 已有 443(证书)却仍 override 443->其它端口 → 流量必不通
            stale = [p for p in (eg.get("PortOverrides") or [])
                     if p.get("ListenerPort") == 443 and p.get("EndpointPort") != 443]
            if stale and 443 in elbv2.listener_ports(region, alb_arn):
                add("ga_port_override", "warn",
                    f"ALB 已有 443 listener,但 GA 仍有 PortOverride 443->{stale[0].get('EndpointPort')}"
                    f"(HTTPS ALB 将不通);重跑「创建数据面资源」会自动清除该 override")
    except Exception as e:  # noqa: BLE001
        add("ga_registered", "warn", f"GA 检查异常:{e}")

    # 3) GA SG → ALB SG(缺则自动补,幂等去重)。端口随 GA 实际转发口:ALB 有 443 → 443,否则 80
    try:
        ga_traffic_port = 443 if 443 in elbv2.listener_ports(region, alb_arn) else ALB_LISTENER_PORT
    except Exception:  # noqa: BLE001
        ga_traffic_port = ALB_LISTENER_PORT
    ga_sg = ga.find_global_accelerator_sg(region, vpc_id)
    if not ga_sg:
        add("ga_sg", "warn", "GA 托管 SG 尚未出现(ALB 注册 client-IP 保留后才生成);稍后重试校验")
    else:
        fixed = False
        for asg_sg in (alb_sgs or []):
            if ec2.sg_allows_from(region, asg_sg, ga_sg, ga_traffic_port):
                continue
            if autofix:
                ec2.authorize_ingress_from_sg(region, asg_sg, ga_sg, ga_traffic_port, "from GlobalAccelerator SG", dry_run=False)
                fixed = True
            else:
                add("ga_to_alb_sg", "warn", f"ALB SG {asg_sg} 未放行 GA SG :{ga_traffic_port}")
        add("ga_to_alb_sg", "fixed" if fixed else "ok",
            "已补 GA→ALB 安全组放行(无重复)" if fixed else "GA→ALB 安全组已放行")

    # 4) ALB SG → 节点 SG :serving_port
    if node_sg_id:
        need = [a for a in (alb_sgs or []) if not ec2.sg_allows_from(region, node_sg_id, a, serving_port)]
        if need and autofix:
            for a in need:
                ec2.authorize_ingress_from_sg(region, node_sg_id, a, serving_port, "from ALB SG", dry_run=False)
            add("alb_to_node_sg", "fixed", f"已补 ALB→节点 SG 放行 :{serving_port}")
        else:
            add("alb_to_node_sg", "ok", f"节点 SG 已放行 ALB :{serving_port}")

    # 5) 私有子网出网(硬前提)—— 只告警,平台不建 NAT
    if asg_subnet_ids:
        try:
            ec2c = elbv2.client("ec2", region)
            egress = ec2._subnet_egress(ec2c, vpc_id, asg_subnet_ids)
            bad = [sid for sid, v in egress.items() if v["egress"] == "none"]
            pub = [sid for sid, v in egress.items() if v["public"]]
            if bad:
                add("private_egress", "warn",
                    f"子网 {bad} 无 0.0.0.0/0→NAT 且未见 NAT 出网;实例将无法拉模型/推指标/SSM。请在该 VPC 加 NAT 或 S3/DynamoDB/SSM VPC endpoint(平台不代建)")
            elif pub:
                add("private_egress", "warn", f"子网 {pub} 是公有子网(ASG 期望私有);如无意暴露请改选私有子网")
            else:
                add("private_egress", "ok", "ASG 子网具备 NAT 出网")
        except Exception as e:  # noqa: BLE001
            add("private_egress", "warn", f"子网出网检查异常:{e}")
    return out


def provision_region(region: str, ami_id: str, *, mode: str = "auto", vpc_id: str | None = None,
                     subnet_ids: list[str] | None = None, asg_subnet_ids: list[str] | None = None,
                     alb_arn: str | None = None, ga_accelerator_arn: str | None = None,
                     sg_id: str | None = None, key_name: str | None = None, serving_port: int = 8000,
                     health_path: str = "/health", metrics_port: int = 8000,
                     dry_run: bool = True, progress=None) -> dict:
    s = get_settings()
    steps: list[dict] = []

    def emit(item: dict) -> None:  # 记录一步 + 实时上报(供前端轮询显示进度)
        steps.append(item)
        if progress:
            try:
                progress(item)
            except Exception:  # noqa: BLE001  进度回调失败绝不影响主流程
                pass

    # ===== BYO 模式:用现有公网 ALB + 选定 GA,ASG 放私有子网 =====
    if mode == "byo":
        return _provision_byo(region, ami_id, vpc_id=vpc_id, asg_subnet_ids=asg_subnet_ids,
                              alb_arn=alb_arn, ga_accelerator_arn=ga_accelerator_arn, sg_id=sg_id,
                              key_name=key_name, serving_port=serving_port, health_path=health_path,
                              metrics_port=metrics_port, dry_run=dry_run, emit=emit, steps=steps, s=s)

    # ===== auto 模式(原有全自动):=====
    # GA:CDK 不再建 GA → 优先用界面所选/Config 的 GA,回落 env,再回落 DRYRUN 占位(与 byo 路径一致)
    ga_arn = ga_accelerator_arn or s.ga_accelerator_arn or "arn:aws:globalaccelerator::DRYRUN"
    # 1) VPC/子网
    if vpc_id and subnet_ids:
        net = {"vpc_id": vpc_id, "subnets": [{"subnet_id": x} for x in subnet_ids]}
        emit({"vpc": "use-existing", "vpc_id": vpc_id, "subnets": subnet_ids})
    else:
        net = ec2.create_vpc(region, dry_run=dry_run)
        emit({"vpc": net})
        vpc_id = net.get("vpc_id", "vpc-DRYRUN")
        subnet_ids = [x["subnet_id"] for x in net.get("subnets", [])] or ["subnet-DRYRUN"]

    # 2) 节点 SG:选现有(已在 API 层校验无 0.0.0.0/0)或自动创建锁定策略
    if sg_id:
        emit({"sg": {"use-existing": sg_id, "note": "已校验无 0.0.0.0/0;metrics + GA 入站将追加到该 SG"}})
        # 复用现有 SG 也补 metrics 抓取入站(与自动创建路径一致),否则监控页抓不到每台 /metrics
        emit({"sg_metrics": ec2.authorize_metrics_ingress(region, sg_id, metrics_port, s.control_plane_egress_cidr, dry_run=dry_run)})
    else:
        sg = ec2.create_node_sg(region, vpc_id, serving_port, metrics_port,
                                s.control_plane_egress_cidr, dry_run=dry_run)
        emit({"sg": sg})
        sg_id = sg.get("sg_id", "sg-DRYRUN")

    # 3) 密钥:选现有;留空 = 不注入密钥(无 SSH)
    if key_name:
        emit({"key": {"use-existing": key_name}})
    else:
        emit({"key": {"none": "不注入密钥(实例无 SSH key)"}})
        key_name = None

    # 3.5) 机型:读平台配置的优先级(按区覆盖→全局→默认;顺序即优先级),再按该区实际供给过滤容错。
    want_types = configured_types(region)
    valid_types = ec2.offered_instance_types(region, want_types) if not dry_run else want_types
    skipped = [t for t in want_types if t not in valid_types]
    if not valid_types:
        raise RuntimeError(f"{region} 不提供任何目标机型 {want_types},无法创建 GPU ASG(该区跳过或改用其他区)。")
    emit({"instance_types": {"used": valid_types, "skipped": skipped, "source": "config.instance_type_priority"}})

    # 4) Launch Template(名称确定 → 幂等,重试追加新版本;ASG overrides 覆盖全部可用机型)
    lt = ec2.create_launch_template(region, f"nlp-lt-{region}", ami_id, valid_types[0],
                                    sg_id, key_name,
                                    s.gpu_node_instance_profile_arn or "arn:aws:iam::DRYRUN:instance-profile/x",
                                    dry_run=dry_run)
    emit({"launch_template": lt})
    lt_id = lt.get("lt_id", "lt-DRYRUN")

    # 5) ALB + TG(ALB 每 AZ 只能挂 1 个子网 → 按 AZ 去重;ASG 仍用全部子网以覆盖更多 AZ)
    alb_subnets = subnet_ids if dry_run else ec2.one_subnet_per_az(region, subnet_ids)
    if not dry_run and len(alb_subnets) < 2:
        raise RuntimeError(f"ALB 需要至少 2 个不同 AZ 的子网,当前仅 {len(alb_subnets)} 个可用 AZ(所选子网:{subnet_ids})。请在步骤 2 至少选择 2 个不同可用区的子网。")
    if len(alb_subnets) != len(subnet_ids):
        emit({"alb_subnets": {"used_one_per_az": alb_subnets, "from": subnet_ids}})
    alb = elbv2.create_alb_with_tg(region, vpc_id, alb_subnets, sg_id, serving_port, health_path, dry_run=dry_run)
    emit({"alb": alb})

    # 5b) ALB 与节点同处一个 SG → 自引用规则放行 serving_port,使 ALB 可访问目标并做健康检查
    emit({"self_ingress": ec2.authorize_self_ingress(region, sg_id, serving_port, dry_run=dry_run)})

    # 6) 注册 GA endpoint(client IP preservation;端口自适应:平台自建 ALB 只有 HTTP:80 → 443->80 映射)
    reg = ga.register_alb(ga_arn, region,
                          alb.get("alb_arn", "arn:alb:DRYRUN"),
                          listener_port=443, endpoint_port=ALB_LISTENER_PORT, dry_run=dry_run)
    emit({"ga_register": reg})
    ga_traffic_port = reg.get("endpoint_port", ALB_LISTENER_PORT)

    # 7) 轮询 GlobalAccelerator SG → 引用它放行 GA 入站到实际转发端口(兼作 TCP 健康检查口)
    if dry_run:
        emit({"ga_ingress": {"planned": "wait GlobalAccelerator SG then authorize ingress (%d)" % ga_traffic_port}})
    else:
        ga_sg = ga.wait_global_accelerator_sg(region, vpc_id)
        if not ga_sg:
            emit({"ga_ingress": {"error": "GlobalAccelerator SG 未在超时内出现"}})
        else:
            emit({"ga_ingress": ga.authorize_ga_ingress(region, sg_id, ga_sg, [ga_traffic_port], dry_run=False)})

    # 8) GPU ASG(100% 按需,机型按优先级 prioritized,desired=0)。不再创建 Spot ASG。
    od = autoscaling.create_od_asg(region, f"nlp-od-{region}", lt_id, subnet_ids, alb.get("tg_arn", "tg-DRYRUN"), types=valid_types, dry_run=dry_run)
    if od.get("note") == "already exists (reused)":
        # 幂等复用:就地刷新机型优先级(改配置后重跑「创建资源」即生效)
        od = {**od, "types_update": autoscaling.update_asg_types(region, f"nlp-od-{region}", lt_id, valid_types, dry_run=dry_run)}
    emit({"gpu_asg": od})

    return {"region": region, "dry_run": dry_run, "vpc_id": vpc_id, "steps": steps}
