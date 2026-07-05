"""Agent 决策 / 动作审计 API:读取 ActionsAudit(规则与 Agent 的每次变更,含前后值与原因)。"""
import time
from fastapi import APIRouter
from pydantic import BaseModel
from ..db.dynamo import get_dynamo
from ..services.aws.session import client

router = APIRouter(prefix="/api")


class ModelTestReq(BaseModel):
    model_id: str


@router.post("/agent/test-model")
def test_model(req: ModelTestReq):  # 同步 def → FastAPI 放线程池执行,不阻塞事件循环
    """用一次极短 Converse 调用验证该 Bedrock 模型 ID 可用(可达 + 有权限)。返回耗时或错误。"""
    br = client("bedrock-runtime", "us-east-1")
    t0 = time.time()
    try:
        # 不传 temperature:新模型(opus-4.8 / sonnet-5)已弃用该参数,传了会 ValidationException
        r = br.converse(modelId=req.model_id,
                        messages=[{"role": "user", "content": [{"text": "ping"}]}],
                        inferenceConfig={"maxTokens": 16})
        ms = int((time.time() - t0) * 1000)
        txt = "".join(b.get("text", "") for b in r["output"]["message"]["content"])
        return {"ok": True, "latency_ms": ms, "sample": txt[:80]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@router.get("/agent/actions")
async def agent_actions(date: str | None = None):
    # date=YYYY-MM-DD(UTC)→ 按天高效 query;否则返回近期(scan)。只展示结构化审计(含 source),
    # 重构前旧格式记录仍留表中(审计不删)但不在此运维视图显示。
    d = get_dynamo()
    items = d.list_actions_by_date(date) if date else d.list_actions(limit=2000)
    structured = [a for a in items if a.get("source")]
    return {"actions": structured[:300], "date": date, "legacy_hidden": len(items) - len(structured)}


@router.get("/runs")
async def runs():
    return {"runs": get_dynamo().list_runs()}
