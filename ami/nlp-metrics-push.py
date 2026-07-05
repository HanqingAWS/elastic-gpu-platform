#!/usr/bin/env python3
"""节点侧指标自推(node-push):每 20s 读 localhost:8000/metrics(vLLM Prometheus),
本地算 QPS / tok-s / 延迟 p50·p95,写一行到 DynamoDB MetricsRollup(控制面 us-east-1)。
监控页/后端 /api/metrics 读 MetricsRollup —— 无需控制面跨 VPC 抓取。

已在 i-0aea6134519f7dcec 实测通过(100QPS 负载下 qps~100 / tok-s~1100 / p50 300ms / p95 500ms)。
部署:AMI 内装为 systemd 服务 nlp-metrics-push.service(见 ami/nlp-metrics-push.service),enabled。
IAM:GPU 节点角色需 dynamodb:PutItem on nlp-dev-metrics-rollup(已加内联策略 nlp-metrics-put)。
"""
import urllib.request as U, time, re, subprocess, json

TABLE = "nlp-dev-metrics-rollup"
TABLE_REGION = "us-east-1"          # 控制面 DynamoDB 固定在 us-east-1(节点跨区写)
INTERVAL = 20


def imds(path: str) -> str:
    tok = U.urlopen(U.Request("http://169.254.169.254/latest/api/token", method="PUT",
                              headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"}), timeout=2).read().decode()
    return U.urlopen(U.Request("http://169.254.169.254/latest/meta-data/" + path,
                               headers={"X-aws-ec2-metadata-token": tok}), timeout=2).read().decode()


REGION = imds("placement/region")
IID = imds("instance-id")


def scrape() -> dict:
    txt = U.urlopen("http://localhost:8000/metrics", timeout=5).read().decode()
    v = {"req": 0.0, "gen": 0.0, "lat_sum": 0.0, "lat_cnt": 0.0, "buckets": {}}
    for ln in txt.splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        try:
            val = float(ln.rsplit(" ", 1)[1])
        except Exception:
            continue
        if ln.startswith("vllm:request_success_total"):
            v["req"] += val
        elif ln.startswith("vllm:generation_tokens_total"):
            v["gen"] += val
        elif ln.startswith("vllm:e2e_request_latency_seconds_sum"):
            v["lat_sum"] += val
        elif ln.startswith("vllm:e2e_request_latency_seconds_count"):
            v["lat_cnt"] += val
        elif ln.startswith("vllm:e2e_request_latency_seconds_bucket"):
            m = re.search(r'le="([^"]+)"', ln)
            if m:
                le = m.group(1)
                v["buckets"][le] = v["buckets"].get(le, 0) + val
    return v


def pct(q: float, cb: dict, pb: dict) -> float:
    """从直方图 delta 近似分位(le 边界),返回毫秒。"""
    tot = cb.get("+Inf", 0) - pb.get("+Inf", 0)
    if tot <= 0:
        return 0.0
    for le in sorted(cb.keys(), key=lambda x: float("inf") if x == "+Inf" else float(x)):
        if (cb.get(le, 0) - pb.get(le, 0)) >= q * tot:
            return 0.0 if le == "+Inf" else round(float(le) * 1000, 1)
    return 0.0


def main():
    # 等 vLLM 就绪(冷启动加载模型可达 ~20min):首次抓取失败则重试,不崩溃退出
    prev = None
    while prev is None:
        try:
            prev = scrape()
        except Exception:  # noqa: BLE001
            time.sleep(INTERVAL)
    pt = time.time()
    while True:
        time.sleep(INTERVAL)
        try:
            cur = scrape()
            now = time.time()
            dt = max(1e-6, now - pt)
            qps = max(0, cur["req"] - prev["req"]) / dt
            toks = max(0, cur["gen"] - prev["gen"]) / dt
            item = {
                "instance_id": {"S": IID}, "ts": {"N": str(int(now))},
                "ttl": {"N": str(int(now) + 30 * 86400)}, "region": {"S": REGION},
                "qps": {"N": str(round(qps, 2))}, "tokens_per_sec": {"N": str(round(toks, 1))},
                "latency_p50": {"N": str(pct(0.5, cur["buckets"], prev["buckets"]))},
                "latency_p95": {"N": str(pct(0.95, cur["buckets"], prev["buckets"]))},
            }
            subprocess.run(["aws", "dynamodb", "put-item", "--region", TABLE_REGION,
                            "--table-name", TABLE, "--item", json.dumps(item)], timeout=15)
            prev = cur
            pt = now
        except Exception as e:  # noqa: BLE001
            print("push err", e, flush=True)


if __name__ == "__main__":
    main()
