"""网络管理 API:三区 VPC/子网发现与选择保存(NetworkSelections)。provisioning 见 provisioning.py。"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..db.dynamo import get_dynamo

router = APIRouter(prefix="/api")


class NetworkSelection(BaseModel):
    region: str
    vpc_id: str | None = None
    subnet_ids: list[str] = []
    sg_id: str | None = None        # 选现有安全组;空=自动创建
    key_name: str | None = None     # 选现有密钥对;空=自动创建
    create_new: bool = False        # True = provision 时新建 VPC
    note: str | None = None


@router.get("/network")
async def list_network():
    return {"selections": get_dynamo().list_network()}


@router.put("/network")
async def put_network(sel: NetworkSelection):
    return get_dynamo().put_network(sel.model_dump())
