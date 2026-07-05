"""Cognito JWT 校验。改编自 sample-bedrock-api-proxy/admin_portal/backend/utils/jwt_validator.py,
改用 python-jose。JWKS 缓存 1 小时。"""
from __future__ import annotations
import time
from typing import Any
import httpx
from jose import jwt
from jose.utils import base64url_decode  # noqa: F401  (确保依赖存在)


class JWTError(Exception):
    pass


class CognitoJWTValidator:
    def __init__(self, user_pool_id: str, client_id: str, region: str):
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.region = region
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self._jwks: dict[str, Any] | None = None
        self._jwks_at: float = 0.0

    def _jwks_keys(self) -> list[dict]:
        if self._jwks is None or time.time() - self._jwks_at > 3600:
            url = f"{self.issuer}/.well-known/jwks.json"
            self._jwks = httpx.get(url, timeout=5).json()
            self._jwks_at = time.time()
        return self._jwks["keys"]

    def _signing_key(self, token: str) -> dict:
        kid = jwt.get_unverified_header(token).get("kid")
        for k in self._jwks_keys():
            if k["kid"] == kid:
                return k
        raise JWTError("signing key not found")

    def validate(self, token: str) -> dict[str, Any]:
        key = self._signing_key(token)
        try:
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.issuer,
                options={"verify_aud": False, "verify_exp": True, "verify_iss": True},
            )
        except Exception as e:  # noqa: BLE001
            raise JWTError(str(e))
        # id token 校验 aud=client_id;access token 校验 client_id 声明
        use = claims.get("token_use")
        if use == "id" and claims.get("aud") != self.client_id:
            raise JWTError("invalid audience")
        if use == "access" and claims.get("client_id") != self.client_id:
            raise JWTError("invalid client_id")
        return claims

    @staticmethod
    def user_info(claims: dict) -> dict:
        return {
            "username": claims.get("cognito:username") or claims.get("username"),
            "email": claims.get("email"),
            "sub": claims.get("sub"),
        }
