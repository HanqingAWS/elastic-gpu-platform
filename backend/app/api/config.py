"""平台配置 + 队列只读(供 UI)。provisioning/调度写操作在 P1/P2 加入。"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..core.config import get_settings
from ..db.dynamo import get_dynamo, DEFAULT_REGIONS

router = APIRouter(prefix="/api")


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
    # 全按需模式:不展示已弃用的 spot ASG(保留在 AWS 侧 desired=0 仅为回退)
    fs = [f for f in d.list_fleet_state() if f.get("asg_kind") != "spot"]
    return {"fleet_state": fs, "instances": d.list_instances()}


@router.delete("/config/regions/{region}")
async def delete_region(region: str):
    """从区域注册表移除一个区(仅删配置项;AWS 资源需另行拆除 —— 前端应先 disable + desired=0)。"""
    return get_dynamo().delete_config_region(region)


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
