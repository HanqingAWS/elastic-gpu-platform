"""定时活动事件(活动窗口排期)API。支持多排期:列出 / 新增-更新 / 删除。"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..db.dynamo import get_dynamo

router = APIRouter(prefix="/api")


class Schedule(BaseModel):
    schedule_id: str = "default"
    name: str | None = None
    start: str = "17:00"          # HH:MM
    end: str = "24:00"            # 24:00 = 次日 0 点
    timezone: str = "Asia/Shanghai"
    days: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    activity_count: int = 2       # 活动数量(在基础数量之上叠加;总数量 = 基础 + 活动)
    od_max: int = 4               # On-Demand 兜底上限
    prewarm_min: int = 25         # 窗口前预热提前量(分钟)
    enabled: bool = True


@router.get("/schedules")
async def get_schedules():
    return {"schedules": get_dynamo().list_schedules()}


@router.put("/schedules")
async def put_schedule(s: Schedule):
    return get_dynamo().put_schedule(s.model_dump())


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    get_dynamo().put_schedule_delete(schedule_id)
    return {"deleted": schedule_id}
