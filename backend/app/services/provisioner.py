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


def provision_region(region: str, ami_id: str, *, vpc_id: str | None = None,
                     subnet_ids: list[str] | None = None, sg_id: str | None = None,
                     key_name: str | None = None, serving_port: int = 8000,
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

    # 6) 注册 GA endpoint(client IP preservation;GA 443 → ALB 80 端口映射)
    reg = ga.register_alb(s.ga_accelerator_arn or "arn:aws:globalaccelerator::DRYRUN", region,
                          alb.get("alb_arn", "arn:alb:DRYRUN"),
                          listener_port=443, endpoint_port=ALB_LISTENER_PORT, dry_run=dry_run)
    emit({"ga_register": reg})

    # 7) 轮询 GlobalAccelerator SG → 引用它放行 GA 入站到 ALB 监听端口(80,兼作 TCP 健康检查口)
    if dry_run:
        emit({"ga_ingress": {"planned": "wait GlobalAccelerator SG then authorize ingress (%d)" % ALB_LISTENER_PORT}})
    else:
        ga_sg = ga.wait_global_accelerator_sg(region, vpc_id)
        if not ga_sg:
            emit({"ga_ingress": {"error": "GlobalAccelerator SG 未在超时内出现"}})
        else:
            emit({"ga_ingress": ga.authorize_ga_ingress(region, sg_id, ga_sg, [ALB_LISTENER_PORT], dry_run=False)})

    # 8) GPU ASG(100% 按需,机型按优先级 prioritized,desired=0)。不再创建 Spot ASG。
    od = autoscaling.create_od_asg(region, f"nlp-od-{region}", lt_id, subnet_ids, alb.get("tg_arn", "tg-DRYRUN"), types=valid_types, dry_run=dry_run)
    if od.get("note") == "already exists (reused)":
        # 幂等复用:就地刷新机型优先级(改配置后重跑「创建资源」即生效)
        od = {**od, "types_update": autoscaling.update_asg_types(region, f"nlp-od-{region}", lt_id, valid_types, dry_run=dry_run)}
    emit({"gpu_asg": od})

    return {"region": region, "dry_run": dry_run, "vpc_id": vpc_id, "steps": steps}
