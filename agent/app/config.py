"""Agent 服务配置(与后端共用同一批环境变量,由 ECS commonEnv 注入)。"""
from __future__ import annotations
import os


class Cfg:
    aws_region = os.getenv("AWS_REGION", "us-east-1")
    regions = [r.strip() for r in os.getenv("DATA_PLANE_REGIONS", "us-east-1,us-east-2,us-west-2").split(",") if r.strip()]
    default_target = int(os.getenv("DEFAULT_TARGET_COUNT", "2"))
    ga_accelerator_arn = os.getenv("GA_ACCELERATOR_ARN")
    # 表名
    table_config = os.getenv("TABLE_CONFIG", "nlp-dev-config")
    table_schedules = os.getenv("TABLE_SCHEDULES", "nlp-dev-schedules")
    table_fleetstate = os.getenv("TABLE_FLEETSTATE", "nlp-dev-fleet-state")
    table_instanceinventory = os.getenv("TABLE_INSTANCEINVENTORY", "nlp-dev-instance-inventory")
    table_metricsrollup = os.getenv("TABLE_METRICSROLLUP", "nlp-dev-metrics-rollup")
    table_actionsaudit = os.getenv("TABLE_ACTIONSAUDIT", "nlp-dev-actions-audit")
    table_spotevents = os.getenv("TABLE_SPOTEVENTS", "nlp-dev-spot-events")
    table_costrollup = os.getenv("TABLE_COSTROLLUP", "nlp-dev-cost-rollup")
    # 兜底与预热
    od_max = int(os.getenv("OD_MAX", "8"))
    prewarm_lead_min = int(os.getenv("PREWARM_LEAD_MIN", "25"))
    backfill_grace_sec = int(os.getenv("BACKFILL_GRACE_SEC", "600"))  # 窗口开始后多久 spot 不足才补 OD
    # Spot 回收事件 / 监控数据留存天数
    retention_days = int(os.getenv("RETENTION_DAYS", "90"))


CFG = Cfg()
