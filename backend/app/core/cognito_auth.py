"""Cognito 鉴权中间件。改编自 sample-bedrock-api-proxy/admin_portal/backend/middleware/cognito_auth.py。
未配置 Cognito 时(本地开发)放行为 dev-user。"""
from __future__ import annotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from .config import get_settings
from .jwt_validator import CognitoJWTValidator, JWTError

SKIP = {"/health", "/docs", "/openapi.json", "/api/auth/config"}


class CognitoAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        s = get_settings()
        self.configured = s.cognito_configured
        self.validator = (
            CognitoJWTValidator(s.cognito_user_pool_id, s.cognito_client_id, s.cognito_region)
            if self.configured else None
        )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 只拦 /api/*(除 /api/auth/config);静态 UI、/health、根路径放行
        if not path.startswith("/api") or path in SKIP or request.method == "OPTIONS":
            return await call_next(request)
        if not self.configured:
            request.state.user = {"username": "dev-user", "development_mode": True}
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"detail": "missing bearer token"}, status_code=401)
        try:
            claims = self.validator.validate(auth[7:])
        except JWTError as e:
            return JSONResponse({"detail": f"invalid token: {e}"}, status_code=401)
        request.state.user = CognitoJWTValidator.user_info(claims)
        return await call_next(request)
