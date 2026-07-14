"""Strands Agent 装配(懒加载,避免启动超时)。模式参考 mysql-redshift-agent/agent/app.py。
Agent 只在被标记的边缘态由控制循环唤起,给出建议/动作;常态由确定性规则跑。"""
from __future__ import annotations
import os
from .tools.aws_actions import AGENT_TOOLS

_agent = None
_agent_model = None  # 已装配 agent 所用的 model_id
DEFAULT_MODEL = "global.anthropic.claude-opus-4-8"

SYSTEM_PROMPT = """你是 NLP-Platform 的多区域 GPU Spot 调度决策助手。
目标:在活动窗口内,用最少成本让全局健康台数达到目标(默认 2 台),p4de 优先、p4d 兜底,Spot 优先、
On-Demand 兜底,us-east-1 优先。你只能通过提供的工具改动 ASG desired / 触发 OD 兜底 / 调 GA 权重,
所有动作都有护栏(clamp<=8、冷却、每tick上限、审计)。当规则难覆盖(Spot 大面积回收、多区
部分短缺、区域抖动、成本/可用性权衡)时,你给出稳健、保守的动作,并解释理由。"""


def _resolve_model_id() -> str:
    """UI(Config 表)> 环境变量 > 默认。每次决策时读取,支持运行时改模型无需重部署。"""
    try:
        from . import state
        m = state.get_config().get("agent_model_id")
        if m:
            return str(m)
    except Exception:  # noqa: BLE001
        pass
    return os.getenv("AGENT_MODEL_ID", DEFAULT_MODEL)


def get_agent():
    global _agent, _agent_model
    model_id = _resolve_model_id()
    if _agent is None or _agent_model != model_id:  # 模型变了就重建
        from strands import Agent  # type: ignore
        from strands.models import BedrockModel  # type: ignore
        model = BedrockModel(model_id=model_id, region_name=os.getenv("AWS_REGION", "us-east-1"))
        _agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=AGENT_TOOLS)
        _agent_model = model_id
    return _agent


def decide(context: str) -> str:
    """把当前(边缘态)上下文交给 Agent 决策,返回其说明/动作结果文本。"""
    return str(get_agent()(context))
