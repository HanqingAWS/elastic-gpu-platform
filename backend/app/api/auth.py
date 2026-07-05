"""前端拿 Cognito 配置 + 校验会话。注册/登录本身由前端 Amplify 直连 Cognito。"""
from fastapi import APIRouter, Request
from ..core.config import get_settings

router = APIRouter(prefix="/api/auth")


@router.get("/config")
async def auth_config():
    s = get_settings()
    return {
        "userPoolId": s.cognito_user_pool_id,
        "clientId": s.cognito_client_id,
        "region": s.cognito_region,
        "configured": s.cognito_configured,
    }


@router.get("/me")
async def me(request: Request):
    return {"user": getattr(request.state, "user", None)}
