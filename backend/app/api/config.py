"""平台配置 + 队列只读(供 UI)。provisioning/调度写操作在 P1/P2 加入。"""
import threading
from fastapi import APIRouter
from pydantic import BaseModel
from ..core.config import get_settings
from ..db.dynamo import get_dynamo, DEFAULT_REGIONS
from ..services import provisioner

router = APIRouter(prefix="/api")


def _teardown_region_bg(region: str, vpc_id: str | None):
    """后台硬删除该区 AWS 资源(GA EG/ALB/TG/ASG/LT,保留 VPC)。best-effort,日志留痕。"""
    try:
        r = provisioner.deprovision_region(region, vpc_id=vpc_id)
        print(f"[deprovision] {region}: {r.get('steps')}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[deprovision] {region} error: {e}", flush=True)


class RegionConfig(BaseModel):
    ami_arn: str | None = None
    serving_port: int = 8000
    health_path: str = "/health"
    metrics_port: int = 8000
    enabled: bool = True
    label: str | None = None                  # 显示名(如"斯德哥尔摩")
    priority: int | None = None               # 拉起优先级,升序=越优先(0 最高)
    instance_types: list[str] | None = None   # 按区机型覆盖(空=继承全局 instance_type_priority)


class PlatformConfig(BaseModel):
    # 全部可选:PUT 只合并显式传入的字段(环境向导写 regions,定时活动写 base_count,互不覆盖)。
    base_count: int | None = None            # 基础(常驻)台数,活动窗口在此之上叠加
    regions: dict[str, RegionConfig] | None = None
    instance_type_priority: list[str] | None = None
    timezone: str | None = None
    agent_enabled: bool | None = None         # false=暂停(只观测不干预)
    agent_model_id: str | None = None         # Agent 决策用的 Bedrock model id
    ga_accelerator_arn: str | None = None     # 所选 GA(默认平台的);agent 权重逻辑读它


@router.get("/config")
async def get_config():
    return get_dynamo().get_config()


@router.put("/config")
async def put_config(cfg: PlatformConfig):
    patch = cfg.model_dump(exclude_unset=True)  # 只合并显式传入的键
    return get_dynamo().put_config(patch)


@router.get("/fleet")
async def fleet():
    d = get_dynamo()
    # 只展示当前注册表里的区(过滤掉已移除区遗留的 FleetState/实例行,如已删的测试区)
    active = set((d.get_config().get("regions") or {}).keys())
    # 全按需模式:不展示已弃用的 spot ASG(保留在 AWS 侧 desired=0 仅为回退)
    fs = [f for f in d.list_fleet_state()
          if f.get("asg_kind") != "spot" and (not active or f.get("region") in active)]
    inst = [i for i in d.list_instances() if (not active or i.get("region") in active)]
    return {"fleet_state": fs, "instances": inst}


@router.delete("/config/regions/{region}")
async def delete_region(region: str):
    """移除一个区:从注册表删除(+ 清 FleetState)+ 后台硬删除该区 AWS 资源
    (GA endpoint group / ALB / TG / ASG / LT),**保留 VPC/子网/SG**(可复用)。"""
    d = get_dynamo()
    vpc = ((d.get_config().get("regions") or {}).get(region) or {}).get("provisioned_vpc")
    d.delete_config_region(region)
    threading.Thread(target=_teardown_region_bg, args=(region, vpc), daemon=True).start()
    return {"removed": region, "teardown": "started", "vpc_kept": True}


@router.get("/regions")
async def regions():
    """Config 驱动的活动区域列表:仅 enabled 项,按 (priority, region) 排序,返回对象数组。
    Config.regions 为空时回退 env 基线(经 DEFAULT_REGIONS 映射 label/priority)。"""
    regs = (get_dynamo().get_config().get("regions") or {})
    out = []
    for r, rc in regs.items():
        if not isinstance(rc, dict) or not rc.get("enabled", True):
            continue
        d = DEFAULT_REGIONS.get(r, {})
        out.append({
            "region": r,
            "label": rc.get("label") or d.get("label", r),
            "priority": int(rc.get("priority", d.get("priority", 99))),
            "enabled": True,
            "instance_types": rc.get("instance_types") or None,
        })
    if not out:  # 回退:env 基线
        for r in get_settings().regions:
            d = DEFAULT_REGIONS.get(r, {})
            out.append({"region": r, "label": d.get("label", r),
                        "priority": d.get("priority", 99), "enabled": True, "instance_types": None})
    out.sort(key=lambda x: (x["priority"], x["region"]))
    return {"regions": out}
