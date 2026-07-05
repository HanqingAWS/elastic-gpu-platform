"""GA 权重:各区 endpoint group 的 TrafficDialPercentage 与该区健康台数成正比,
目标是「每台节点获得大致相同的流量」——跨区由 dial 按台数占比分配,区内由 ALB
最少未决请求(LOR)在各台间均摊,二者叠加即每台均摊。

按 peak(台数最多的区)归一化:台数最多的区=100(不必要地下调会白白浪费就近容量),
其余 = round(100 × 本区台数 / 最多区台数),0 台=0(移出轮转)。dial 之比 = 台数之比,
故每台流量 ≈ 常数。**不设任何区域保底**——保底(如旧的 us-east-1=60)会让「薄」区的
单台被灌入过量流量,破坏每台均摊。"""
from __future__ import annotations


def compute_dials(obs_list: list[dict]) -> dict[str, int]:
    healthy = {o["region"]: o["spot_healthy"] + o["od_healthy"] for o in obs_list}
    peak = max(healthy.values(), default=0)
    return {region: (round(100 * h / peak) if (h > 0 and peak) else 0)
            for region, h in healthy.items()}
