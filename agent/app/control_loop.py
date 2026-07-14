"""确定性控制主干(P2):按排期定窗口 → 逐区观测 ASG/健康 → 预热/OD 兜底/归零 → 状态写 DynamoDB。
边缘态交给 Agent(P4)。所有变更经 guardrails(clamp/冷却/每tick上限/窗口守卫/审计)。"""
from __future__ import annotations
import os
import time
from .config import CFG
from . import aws, state, scheduler
from .tools.guardrails import STATE, reset_tick
from .actions import _set_asg_desired

TICK_SEC = int(os.getenv("LOOP_TICK_SEC", "45"))
# 唯一开关:启用(真实调度)/ 暂停(只观测)。无 dry-run 模式。每 tick 从 Config 读 agent_enabled。
ENABLED = True

_target_since: float | None = None   # 目标(target>0)持续到现在的单调起点,用于跨区溢出 / OD 兜底的 grace
_seen_spot_events: set[str] = set()  # 进程内去重,避免每 tick 重复写同一回收事件
PRIORITY = ["eu-north-1", "us-east-1", "us-east-2", "us-west-2"]  # 拉起优先级:EU 客户 → eu-north-1 优先,US 靠后兜底(eu-central-2/苏黎世无 p4d 供给,已排除)
_last_dials: dict[tuple[str, str], int] = {}   # (ga_arn, region) → 上次已下发的 TrafficDial;权重没变才跳过。含 ga_arn:换 GA/EG 后强制重下发(否则旧 GA 的残留值会误跳过新 EG)
_last_desired: dict[str, int] = {}    # 上次已下发的 ASG desired,值没变就不重复调用/审计
_last_accrual: float | None = None    # 上次计量运行时长的单调时钟点(用真实经过时间累加,而非固定 tick)


def _set_asg(region: str, asg_name: str, desired: int, before: int, reason: str):
    """去重包装:仅当目标 desired 确有变化(且非无操作)时才下发 + 写审计,避免每 tick 刷屏。"""
    key = f"{region}:{asg_name}"
    if desired == before or _last_desired.get(key) == desired:
        _last_desired[key] = desired
        return
    _set_asg_desired(region=region, dry_run=False, asg_name=asg_name, desired=desired, before=before, reason=reason)
    _last_desired[key] = desired


def collect_spot_events(region: str):
    """观测被 Spot 回收的实例并写 SpotEvents(TTL 90 天)。"""
    for ev in aws.recent_spot_interruptions(region):
        iid = ev["instance_id"]
        if iid in _seen_spot_events:
            continue
        _seen_spot_events.add(iid)
        state.record_spot_event(region, iid, "reclaimed",
                                az=ev.get("az"), instance_type=ev.get("instance_type"), reason=ev.get("reason"))


def _billed_lifecycle(lc) -> bool:
    lc = str(lc or "")
    return lc.startswith(("InService", "Pending", "Terminating")) or lc == "Warmed:Running"


def _spot_asg(region: str) -> str:
    return f"nlp-spot-{region}"


def _od_asg(region: str) -> str:
    return f"nlp-od-{region}"


def observe_region(region: str) -> dict:
    out = {"region": region, "spot_desired": 0, "spot_healthy": 0, "od_desired": 0, "od_healthy": 0,
           "spot_running": 0, "od_running": 0, "instances": []}
    for kind, name in (("spot", _spot_asg(region)), ("od", _od_asg(region))):
        asg = aws.describe_asg(region, name)
        if not asg:
            continue
        desired = asg.get("DesiredCapacity", 0)
        hmap: dict = {}
        for tg in aws.asg_target_group_arns(asg):
            hmap.update(aws.target_health_map(region, tg))
        insts = aws.asg_instances(region, asg)
        for inst in insts:  # 逐台标注「模型就绪」= ALB target healthy(区别于 EC2/ASG 运行中)
            st = hmap.get(inst["instance_id"])
            inst["target_state"] = st or "-"
            inst["ready"] = (st == "healthy")
        # 健康数只数「本 ASG 自己的」健康实例 —— spot/od 共用同一个 ALB target group,
        # 若直接数 TG 里全部 healthy,会把同一台在 spot 和 od 各计一次(之前"2/1"的根因)。
        healthy = sum(1 for inst in insts if inst["ready"])
        # running = 计费口径:只数产生计算费用的生命周期(InService/Pending/Terminating*、Warmed:Running),
        # 排除 Standby / Warmed:Stopped / Warmed:Hibernated(EBS-only,无计算费)。不看健康。
        out[f"{kind}_running"] = sum(1 for i in insts if _billed_lifecycle(i.get("lifecycle")))
        out[f"{kind}_desired"] = desired
        out[f"{kind}_healthy"] = healthy
        out["instances"].extend(insts)
        # 全按需:只记录 od 的 fleet_state 供展示;仍观测 spot(act_gpu 用它做归零安全网),但不落库/不展示
        if kind == "od":
            state.put_fleet_state(region, kind, desired, healthy)
    # 补开机时间(EC2 LaunchTime;ASG 记录里没有)
    det = aws.instance_details(region, [i["instance_id"] for i in out["instances"]])
    for inst in out["instances"]:
        inst["launch_time"] = det.get(inst["instance_id"], {}).get("launch_time")
    # reconcile 终止:上一 tick 见过、这一 tick 没了 = 已终止 → 补带终止时间的记录
    # (修复"生命周期 Terminating / 状态 Healthy 挂几小时"的陈旧不准)
    _reconcile_terminated(region, out["instances"])
    state.put_instances(region, out["instances"])
    return out


_last_seen: dict = {}  # region -> {instance_id: record};用于识别本 tick 消失(已终止)的实例


def _reconcile_terminated(region: str, current: list[dict]) -> None:
    cur = {i["instance_id"]: i for i in current}
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    for iid, rec in _last_seen.get(region, {}).items():
        if iid not in cur:  # 上 tick 在、这 tick 没了 = 已终止
            current.append({**rec, "lifecycle": "Terminated", "health": "Terminated",
                            "ready": False, "target_state": "-", "terminated_at": now_iso})
    _last_seen[region] = {i["instance_id"]: i for i in current if not i.get("terminated_at")}


def _regions_and_priority(cfg: dict) -> tuple[list[str], list[str]]:
    """从 Config 派生 (动作区集合, 优先级顺序)。
    动作区 = enabled 且**已 provision(有 provisioned_vpc)**的区 —— provisioned-gate:
    避免 enabled 但还没建资源的高优先区在 allocate 里白拿 target(act_gpu 会指向不存在的 ASG)。
    Config 无可用区(或异常)时回退 (CFG.regions env, 模块 PRIORITY)。"""
    regs = cfg.get("regions") or {}
    active = [r for r, rc in regs.items()
              if isinstance(rc, dict) and rc.get("enabled", True) and rc.get("provisioned_vpc")]
    if not active:
        return list(CFG.regions), list(PRIORITY)

    def _prio(r: str) -> int:
        p = (regs.get(r) or {}).get("priority")
        if p is not None:
            try:
                return int(p)
            except (TypeError, ValueError):
                pass
        return PRIORITY.index(r) if r in PRIORITY else 999
    ordered = sorted(active, key=lambda r: (_prio(r), r))
    return ordered, ordered


def allocate(all_obs: list[dict], target: int, overdue: bool, priority: list[str] | None = None) -> dict[str, int]:
    """跨区配额分配(总共 target 台,全按需,按 priority 就近优先):
    us-east-1 与 us-east-2(俄亥俄)同属美东,均为优先区 —— 按序先在美东开满;
    仅当美东两区过 grace 仍凑不满(按需容量不足)时,才溢出到 us-west-2(俄勒冈,较远)。
    缺口用「已拉起(running)」抵扣,而非「已健康」—— 优先区已开出机器(即便还在加载模型未健康)
    就不再往下一区溢出,避免"边启动边多开"(此前 over-provision 的根因)。"""
    obsmap = {o["region"]: o for o in all_obs}
    prio = priority if priority is not None else PRIORITY
    order = [r for r in prio if r in obsmap] + [r for r in obsmap if r not in prio]
    alloc = {r: 0 for r in obsmap}
    remaining = target
    for i, r in enumerate(order):
        running = obsmap[r].get("spot_running", 0) + obsmap[r].get("od_running", 0)  # 已拉起(计费中)台
        if i == 0:
            alloc[r] = target                 # 首优先区(us-east-1):始终目标全部
        elif overdue and remaining > 0:
            alloc[r] = remaining              # 后续区按 PRIORITY 顺序承接缺口(us-east-2 先于 us-west-2)
        else:
            alloc[r] = 0
        remaining = max(0, remaining - min(running, alloc[r]))  # 已拉起的抵扣缺口
    return alloc


def act_gpu(region: str, obs: dict, region_target: int):
    """全按需模式:把该区 GPU(od)ASG desired 维持到其配额;配额=0 则归零。
    另:存量 Spot ASG 永久归零(需求变更 2026-07-04 —— Spot 抢不到,全按需;保留 ASG 便于回退)。"""
    if obs["od_desired"] != region_target:
        reason = "预热 / 维持至该区按需配额" if region_target > 0 else "非优先区 / 高优先区已满足 —— 归零"
        _set_asg(region, _od_asg(region), region_target, obs["od_desired"], reason)
    if obs["spot_desired"] != 0:  # 安全网:任何情况下 Spot 池都不再使用
        _set_asg(region, _spot_asg(region), 0, obs["spot_desired"], "全按需模式 —— Spot 池归零(已弃用)")


def is_edge_state(all_obs: list[dict], target: int) -> bool:
    """规则久攻不下才交给 Agent:目标激活、已过 2×grace、总健康仍 < 目标(各区 Spot 都抢不到且 OD 未补齐)。"""
    if _target_since is None or target <= 0:
        return False
    total = sum(o["spot_healthy"] + o["od_healthy"] for o in all_obs)
    if total >= target:
        return False
    return time.monotonic() - _target_since > 2 * CFG.backfill_grace_sec


def tick():
    reset_tick()
    global ENABLED, _last_accrual, _target_since
    cfg = state.get_config()
    ENABLED = bool(cfg.get("agent_enabled", True))  # 唯一开关:启用/暂停(UI 可改,无需重部署)
    base = int(cfg.get("base_count", 0) or 0)              # 基础(常驻)台数
    regions, priority = _regions_and_priority(cfg)   # 区域+优先级来自 Config(可 UI 改,无需重部署)
    sched = scheduler.evaluate(state.list_schedules())
    active = sched["prewarm"] or sched["window_open"]
    target = base + (sched["activity"] if active else 0)  # 全局总目标(跨区共 N 台)= 基础 + 活动
    sched["target"] = target
    now = time.monotonic()
    # 目标持续计时(跨区溢出 / OD 兜底的 grace 起点)
    _target_since = _target_since if (target > 0 and _target_since is not None) else (now if target > 0 else None)
    overdue = _target_since is not None and (now - _target_since) > CFG.backfill_grace_sec
    # 运行时长按真实经过时间累加(而非固定 tick):首个 tick 只设基准不计;clamp 防暂停后暴增
    elapsed_h = 0.0 if _last_accrual is None else min(now - _last_accrual, 3 * TICK_SEC) / 3600.0
    # 1) 先观测所有区(不动作)
    all_obs = []
    for region in regions:
        try:
            obs = observe_region(region)
            collect_spot_events(region)
            if elapsed_h > 0:  # 计费兜底:运行实例数 × 真实经过小时
                state.add_running_hours(region, obs["spot_running"] * elapsed_h, obs["od_running"] * elapsed_h)
            all_obs.append(obs)
        except Exception as e:  # noqa: BLE001
            print(f"[loop] region {region} observe err: {e}", flush=True)
    _last_accrual = now  # 循环外推进基准:即使某区异常也不丢失/不重复计整段时间
    total_healthy = sum(o["spot_healthy"] + o["od_healthy"] for o in all_obs)
    # 2) 跨区配额分配(美东优先、其余兜底溢出)→ 逐区维持按需 GPU ASG(全按需,无 Spot/兜底二层结构)
    if ENABLED and all_obs:
        alloc = allocate(all_obs, target, overdue, priority)
        for obs in all_obs:
            try:
                act_gpu(obs["region"], obs, alloc.get(obs["region"], 0))
            except Exception as e:  # noqa: BLE001
                print(f"[loop] region {obs['region']} act err: {e}", flush=True)

    # GA 权重:按各区健康台数占比设 TrafficDial。GA ARN 优先读 Config(所选 GA,可含独立 GA),回落 env。
    ga_arn = cfg.get("ga_accelerator_arn") or CFG.ga_accelerator_arn
    if ENABLED and ga_arn and all_obs:
        from .weights import compute_dials
        from .actions import _set_ga_weights
        from .tools.guardrails import GuardrailError
        for region, dial in compute_dials(all_obs).items():
            key = (ga_arn, region)   # 含 GA:换 GA/EG 后强制重下发(旧 GA 残留值不再误跳过新 EG)
            if _last_dials.get(key) == dial:
                continue  # 权重未变化:不重复调用、不刷审计(避免 Agent 日志被 set_ga_weights 刷屏)
            eg = aws.find_endpoint_group_arn(ga_arn, region)
            if not eg:
                continue
            try:
                _set_ga_weights(region=region, dry_run=False, endpoint_group_arn=eg, traffic_dial=dial,
                                before=_last_dials.get(key), reason="按各区健康台数占比重算 TrafficDial")
                _last_dials[key] = dial
            except GuardrailError:
                pass

    if is_edge_state(all_obs, target):
        from .agent import decide
        decide(f"边缘态,请给稳健动作。观测={all_obs} 排期={sched}")
    print(f"[loop] enabled={ENABLED} window={sched['window_open']} prewarm={sched['prewarm']} "
          f"target={target} healthy={total_healthy} overdue={overdue} "
          f"regions={len(regions)} prio={priority}", flush=True)


def run_forever():
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            print(f"[loop] error: {e}", flush=True)
        time.sleep(TICK_SEC)
