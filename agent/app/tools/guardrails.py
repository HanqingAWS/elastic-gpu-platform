"""Agent 变更工具的统一护栏:动作白名单、上下限 clamp、每 tick 次数上限 + 冷却、dry-run、
窗口内才可执行、执行前后写 ActionsAudit。规则与 Agent 共用。"""
from __future__ import annotations
import os
import time
import uuid
import functools
from dataclasses import dataclass, field

ALLOWED_ACTIONS = {"set_asg_desired", "trigger_od_backfill", "rebalance_regions", "set_ga_weights"}
MAX_DESIRED = int(os.getenv("GUARD_MAX_DESIRED", "8"))          # 每 ASG 每区上限(P 配额 768vCPU=8 台)
PER_TICK_ACTION_CAP = int(os.getenv("GUARD_PER_TICK_CAP", "6")) # 每 tick 最多变更次数
COOLDOWN_SEC = int(os.getenv("GUARD_COOLDOWN_SEC", "60"))       # 同一 (action,region) 冷却


@dataclass
class GuardState:
    tick_count: int = 0
    last_action_at: dict[str, float] = field(default_factory=dict)
    window_open: bool = False  # 由调度器在活动窗口内置 True


STATE = GuardState()


class GuardrailError(Exception):
    pass


def clamp_desired(n: int) -> int:
    return max(0, min(MAX_DESIRED, int(n)))


def guarded(action: str):
    """装饰器:给一个"变更"函数加护栏。被装饰函数签名 fn(*, region: str, dry_run: bool, **kw)。"""
    if action not in ALLOWED_ACTIONS:
        raise GuardrailError(f"action not allowlisted: {action}")

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*, region: str = "-", dry_run: bool = False,
                    source: str = "rule", reason: str | None = None, before=None, **kw):
            # source/reason/before 仅用于审计,不透传给底层动作。
            # 无 dry-run 模式:启用/暂停在 control_loop 层控制是否调用;此处只做量级护栏。
            if STATE.tick_count >= PER_TICK_ACTION_CAP and not dry_run:
                raise GuardrailError("per-tick action cap reached")
            key = f"{action}:{region}"
            now = time.time()
            if not dry_run and now - STATE.last_action_at.get(key, 0) < COOLDOWN_SEC:
                raise GuardrailError(f"cooldown active for {key}")
            result = fn(region=region, dry_run=dry_run, **kw)  # 结构化明细
            if not dry_run:
                STATE.tick_count += 1
                STATE.last_action_at[key] = now
            # 结构化审计:每条都能看清 谁/对哪个区哪种ASG/从多少到多少/为什么
            audit = {
                "id": uuid.uuid4().hex,
                "ts": int(now),
                "source": source,                                   # rule | agent
                "action": action,
                "region": region,
                "kind": result.get("kind"),                         # spot | od | ga
                "target": result.get("target"),                     # 资源名(ASG/endpoint group)
                "before": before if before is not None else result.get("before"),
                "after": result.get("after"),
                "unit": result.get("unit"),                         # 台 | %
                "reason": reason,
                "dry_run": dry_run,
                "status": result.get("status"),                     # planned | done
            }
            _write_audit(audit)
            return result
        return wrapper
    return deco


def _write_audit(audit: dict):
    print(f"[AUDIT] {audit}", flush=True)
    try:
        from ..state import write_audit  # 懒加载避免循环导入
        write_audit(audit)
    except Exception:  # noqa: BLE001
        pass


def reset_tick():
    STATE.tick_count = 0
