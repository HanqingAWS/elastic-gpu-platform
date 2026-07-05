"""集中配置(从 ECS task definition 注入的环境变量读取)。"""
from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "us-east-1"
    environment: str = "dev"
    data_plane_regions: str = "us-east-1,us-east-2,us-west-2"
    default_target_count: int = 2

    # Cognito
    cognito_user_pool_id: str | None = None
    cognito_client_id: str | None = None
    cognito_region: str = "us-east-1"

    # Global Accelerator
    ga_accelerator_arn: str | None = None

    # 数据平面 provisioning 用
    gpu_node_instance_profile_arn: str | None = None
    gpu_node_role_arn: str | None = None
    control_plane_sg_id: str | None = None
    # 控制平面出口 IP(NAT EIP)/32,用于数据平面节点 metrics 端口放行。
    # 跨 VPC/区无法用 SG 引用,故用 /32(仍非 0.0.0.0/0)。
    control_plane_egress_cidr: str = "127.0.0.1/32"

    # DynamoDB 表名(TABLE_<LOGICAL> 环境变量)
    table_config: str = "nlp-dev-config"
    table_schedules: str = "nlp-dev-schedules"
    table_runs: str = "nlp-dev-runs"
    table_fleetstate: str = "nlp-dev-fleet-state"
    table_instanceinventory: str = "nlp-dev-instance-inventory"
    table_metricsrollup: str = "nlp-dev-metrics-rollup"
    table_actionsaudit: str = "nlp-dev-actions-audit"
    table_networkselections: str = "nlp-dev-network-selections"
    table_spotevents: str = "nlp-dev-spot-events"
    table_costrollup: str = "nlp-dev-cost-rollup"

    # Spot 回收事件 / 监控数据留存天数
    retention_days: int = 90

    @property
    def regions(self) -> list[str]:
        return [r.strip() for r in self.data_plane_regions.split(",") if r.strip()]

    @property
    def cognito_configured(self) -> bool:
        return bool(self.cognito_user_pool_id and self.cognito_client_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
