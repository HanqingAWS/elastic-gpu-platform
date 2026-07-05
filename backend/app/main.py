"""NLP-Platform 控制平面 Web 后端。"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .core.cognito_auth import CognitoAuthMiddleware
from .api import (
    health, auth, config as config_api, provisioning, schedules,
    metrics, ga, network, agent, spot, cost,
)

app = FastAPI(title="NLP-Platform API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产收紧到前端域名
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CognitoAuthMiddleware)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(config_api.router)
app.include_router(provisioning.router)
app.include_router(schedules.router)
app.include_router(metrics.router)
app.include_router(ga.router)
app.include_router(network.router)
app.include_router(agent.router)
app.include_router(spot.router)
app.include_router(cost.router)


# 托管 React 静态产物(容器内 /app/static;本地无则跳过)。必须在路由注册之后挂载 /。
_static = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="ui")
