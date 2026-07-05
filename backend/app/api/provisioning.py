"""Provisioning 任务流 API:发现 VPC/子网、执行 provision。

provision 是长任务(ALB 就绪 + GA SG 轮询可达数分钟)。它必须异步执行:
- 同步跑会阻塞单 worker 的事件循环 → 整个 UI 卡死 + 超过 CloudFront/ALB 网关超时(504)。
所以 POST 立即返回 run_id,后台线程(非事件循环)跑 provision_region,前端轮询 /provision-status。
"""
import threading
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from ..services.aws import ec2
from ..services import provisioner
from ..db.dynamo import get_dynamo

router = APIRouter(prefix="/api/provisioning")

# 单 uvicorn worker → 进程内状态即可供前端轮询。run_id -> 状态字典。
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()
_RUNS_MAX = 50  # 上限,避免内存无限增长


@router.get("/vpcs")
async def vpcs(region: str = Query(...)):
    return {"region": region, "vpcs": ec2.list_vpcs(region)}


@router.get("/subnets")
async def subnets(region: str = Query(...), vpc_id: str = Query(...)):
    return {"region": region, "vpc_id": vpc_id, "subnets": ec2.list_subnets(region, vpc_id)}


@router.get("/security-groups")
async def security_groups(region: str = Query(...), vpc_id: str = Query(...)):
    return {"region": region, "vpc_id": vpc_id, "security_groups": ec2.list_security_groups(region, vpc_id)}


@router.get("/key-pairs")
async def key_pairs(region: str = Query(...)):
    return {"region": region, "key_pairs": ec2.list_key_pairs(region)}


class ProvisionReq(BaseModel):
    region: str
    ami_id: str
    vpc_id: str | None = None          # 选现有;为空则新建 VPC
    subnet_ids: list[str] | None = None
    sg_id: str | None = None           # 选现有安全组;为空则自动创建(锁定策略)
    key_name: str | None = None        # 选现有密钥对;为空则不注入密钥
    serving_port: int = 8000
    health_path: str = "/health"
    metrics_port: int = 8000
    dry_run: bool = False


def _run_provision(run_id: str, req: ProvisionReq) -> None:
    """后台线程:真正执行 provision(阻塞的 boto3 + 等待器都在此线程,不碰事件循环)。"""
    try:
        result = provisioner.provision_region(
            req.region, req.ami_id, vpc_id=req.vpc_id, subnet_ids=req.subnet_ids,
            sg_id=req.sg_id, key_name=req.key_name,
            serving_port=req.serving_port, health_path=req.health_path,
            metrics_port=req.metrics_port, dry_run=req.dry_run,
            progress=lambda step: _append_step(run_id, step),
        )
        with _RUNS_LOCK:
            _RUNS[run_id].update(status="succeeded", finished=True,
                                 vpc_id=result.get("vpc_id"), steps=result.get("steps", []))
        # 标记该区“资源已创建”(供向导显示资源状态);失败不影响主结果
        try:
            get_dynamo().put_config({"regions": {req.region: {
                "provisioned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "provisioned_vpc": result.get("vpc_id")}}})
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        with _RUNS_LOCK:
            _RUNS[run_id].update(status="failed", finished=True, error=str(e))


def _append_step(run_id: str, step: dict) -> None:
    with _RUNS_LOCK:
        r = _RUNS.get(run_id)
        if r is not None:
            r["steps"].append(step)


@router.post("/provision")
async def provision(req: ProvisionReq):
    # 注:选定安全组含 0.0.0.0/0 时不硬性拒绝,仅由前端提示 + 客户二次确认。
    run_id = uuid.uuid4().hex[:12]
    with _RUNS_LOCK:
        if len(_RUNS) >= _RUNS_MAX:  # 清理已完成的最老记录
            for k in [k for k, v in list(_RUNS.items()) if v.get("finished")][: max(1, _RUNS_MAX // 2)]:
                _RUNS.pop(k, None)
        _RUNS[run_id] = {"run_id": run_id, "region": req.region, "status": "running",
                         "finished": False, "steps": [], "error": None}
    threading.Thread(target=_run_provision, args=(run_id, req), daemon=True).start()
    return {"run_id": run_id, "region": req.region, "status": "running"}


@router.get("/status")
async def region_status(region: str = Query(...), vpc_id: str | None = Query(None)):
    """该区资源真实状态(以 ASG 是否存在为准)。供向导显示“资源已创建/未创建”。"""
    return provisioner.region_status(region, vpc_id=vpc_id)


@router.get("/provision-status")
async def provision_status(run_id: str = Query(...)):
    with _RUNS_LOCK:
        st = _RUNS.get(run_id)
    if not st:
        raise HTTPException(status_code=404, detail="unknown run_id")
    return st
