"""Agent/Scheduler 服务:健康端点(供 ECS 健康检查)+ 后台常驻控制循环。"""
from __future__ import annotations
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .control_loop import run_forever
from .tools.guardrails import STATE


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=run_forever, daemon=True)
    t.start()
    yield


app = FastAPI(title="NLP-Platform Agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "healthy", "window_open": STATE.window_open}


@app.post("/window/open")
async def open_window():
    """调度器在活动窗口开始时调用(打开护栏的窗口守卫)。"""
    STATE.window_open = True
    return {"window_open": True}


@app.post("/window/close")
async def close_window():
    STATE.window_open = False
    return {"window_open": False}
