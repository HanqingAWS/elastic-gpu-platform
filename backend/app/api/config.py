"""平台配置 + 队列只读(供 UI)。provisioning/调度写操作在 P1/P2 加入。"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..core.config import get_settings
from ..db.dynamo import get_dynamo

router = APIRouter(prefix="/api")


class RegionConfig(BaseModel):
    ami_arn: str | None = None
    serving_port: int = 8000
    health_path: str = "/health"
    metrics_port: int = 8000
    enabled: bool = True


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
    return {"fleet_state": d.list_fleet_state(), "instances": d.list_instances()}


@router.get("/regions")
async def regions():
    return {"regions": get_settings().regions}
