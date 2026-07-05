"""按区的 boto3 client 工厂(控制平面用 ECS task role,无显式凭证)。"""
from __future__ import annotations
import functools
import boto3


@functools.lru_cache(maxsize=None)
def client(service: str, region: str):
    return boto3.client(service, region_name=region)
