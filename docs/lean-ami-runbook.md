# Runbook / README — 瘦 AMI 模型分发(GPU 节点)

配套设计见 [`lean-ami-cold-start.md`](./lean-ami-cold-start.md)。本文是**操作手册**。

## TL;DR

GPU 节点镜像**不含模型**。模型放**同区 S3**;实例开机由 `nlp-fetch-model.service`
用 `s5cmd` 并行下载到**本地 NVMe**,再起 vLLM。冷启动从 ~2 小时降到 **~10–12 分钟**,
且不需要 EBS FSR。

## 当前资源(2026-07-04,3 区 = 瘦 AMI **v3**:模型下载 + 指标自推 ✅)

| 项 | us-east-1 | us-east-2 | us-west-2 |
|---|---|---|---|
| S3 桶(已 seed 48GB) | `nlp-models-044324713311-us-east-1` ✅ | `nlp-models-044324713311-us-east-2` ✅ | `nlp-models-044324713311-us-west-2` ✅ |
| **瘦 AMI v3**(+`nlp-metrics-push.service`,LT `$Latest` 指向) | `ami-04eb2af423c9a0201` | `ami-0da90ccd10501e535` | `ami-07ada4dded151b6fe` |
| 瘦 AMI v2(无指标推送,回退用) | `ami-0e64fb7230c15bd6a` | `ami-0fa78624ce67441fc` | `ami-0cc238a7b3b046683` |
| 旧(烘焙)AMI | `ami-092544a71f3ac7ff9` | `ami-098c76b2d92988041` | `ami-0abfe61c977689ffc` |
| Launch Template | `nlp-lt-us-east-1` (lt-06c0a0651b7f3219b) | `nlp-lt-us-east-2` (lt-01bb0ed4310ab4366) | `nlp-lt-us-west-2` (lt-0005e46ff3b42e177) |

**v3 新增 — 节点指标自推(node-push)**:AMI 内 `nlp-metrics-push.service`(源码 `ami/nlp-metrics-push.py`)
每 20s 读 `localhost:8000/metrics`(vLLM Prometheus),本地算 QPS / tok-s / p50·p95(直方图 delta),
写 DynamoDB `nlp-dev-metrics-rollup`(控制面 us-east-1,跨区直写)→ 监控页/`/api/metrics` 直接出数。
无需控制面跨 VPC 抓取、无需开 :8000 入站。IAM:GPU 节点角色内联策略 `nlp-metrics-put`(dynamodb:PutItem)。
启动韧性:vLLM 未就绪(冷启动 ~20min)时静默重试,不崩溃。

> **回退**:把对应 region 的 LT `$Latest` 的 `ImageId` 改回上表"旧烘焙 AMI"即可(那批已含 timeout+prefetch 修复,能起但慢 ~2h)。

模型前缀:`s3://<bucket>/gemma-4-26B-A4B-it/`(2×safetensors + 分词器等,共 ~48GB)。

## 一、给一个新 region 落地(one-time)

```bash
ACCT=044324713311; SRC_REGION=us-east-1; DST=us-east-2   # 举例
SRC_BUCKET=nlp-models-$ACCT-$SRC_REGION
DST_BUCKET=nlp-models-$ACCT-$DST

# 1) 建桶
aws s3api create-bucket --bucket $DST_BUCKET --region $DST \
  --create-bucket-configuration LocationConstraint=$DST   # us-east-1 不加此行

# 2) seed 模型(跨区 server-side 复制,不经本机带宽)
aws s3 sync s3://$SRC_BUCKET/gemma-4-26B-A4B-it/ s3://$DST_BUCKET/gemma-4-26B-A4B-it/ \
  --source-region $SRC_REGION --region $DST

# 3) 复制瘦 AMI 到该区
aws ec2 copy-image --source-region $SRC_REGION --source-image-id ami-0e64fb7230c15bd6a \
  --region $DST --name "nlp-gemma4-lean-onboot-$DST"

# 4) 把该区 Launch Template $Latest 指向瘦 AMI + 同步 Config
aws ec2 create-launch-template-version --region $DST --launch-template-name nlp-lt-$DST \
  --source-version '$Latest' --launch-template-data '{"ImageId":"<lean-ami-in-DST>"}'
# Config.regions[<DST>].ami_arn = <lean-ami-in-DST>  (DynamoDB nlp-dev-config 或 UI 配置向导)
```

> GPU 节点角色的 S3 读权限用通配 `arn:aws:s3:::nlp-models-044324713311-*` 覆盖了所有区,无需每区改 IAM。

## 二、节点开机做了什么(自动)

1. `cloud-init` 跑完 →
2. `nlp-fetch-model.service`(oneshot):取 IMDSv2 token 拿 region → 找本地 NVMe → `mkfs`+挂到
   `/opt/models` → `s5cmd cp s3://nlp-models-<acct>-<region>/gemma-4-26B-A4B-it/* /opt/models/…/`
3. `vllm.service`(`After/Requires=nlp-fetch-model.service`):`docker run vllm/vllm-openai …
   --model /models/gemma-4-26B-A4B-it --data-parallel-size 4 --tensor-parallel-size 2 --port 8000`
4. ALB target 健康检查 `/health` 转 200 → GA 纳入轮转。

## 三、换模型

无需重烘焙 AMI:

- **原地换**:把新权重放到同一前缀(或新前缀),重启实例即重新拉取。
- **新模型**:S3 放到新前缀 `s3://<bucket>/<new-model>/`,改 `nlp-fetch-model.sh` 里的路径
  + `vllm.service` 的 `--model`(可参数化为环境变量/实例标签)。

## 四、排障

| 现象 | 排查 |
|---|---|
| vLLM 不起 / target 一直 unhealthy | `journalctl -u nlp-fetch-model.service`(下载失败会阻断 vLLM);再看 `journalctl -u vllm.service` |
| region 读成空 / 桶名缺后缀 | IMDSv2:确认脚本先 `PUT /latest/api/token`(见设计文档坑 #1) |
| `s5cmd: flag not defined: -region` | 用 `AWS_REGION` 环境变量,勿传 `--region`(坑 #2) |
| AccessDenied 下载 | GPU 节点角色缺 `s3:GetObject/ListBucket` on `nlp-models-*` |
| 找不到 NVMe / 下到 EBS 很慢 | 非 p4de/p4d 无实例存储;脚本回退到 EBS 根盘(慢);确认机型有本地 NVMe |
| 冷启动仍偏慢 | `s5cmd` 加并发,或换更大网络机型,或叠加 EBS FSR |

## 五、成本

- 平时:仅 S3 存储 ~$1/月/区(48GB)。GPU 只在活动窗口按需拉起。
- 对比:烘焙 AMI + FSR 需为每个 AZ×快照 持续付 FSR 费(~$0.75/AZ/小时)。

## 六、实测(us-east-1)

- S3→NVMe 手动下载 48GB:**335s ≈ 5.6min**(c5d.4xlarge/10Gbps)。
- **无人值守开机流程**(瘦 AMI → `nlp-fetch-model.service` 自动挂 NVMe + 下载):
  **实例 launch → 48GB 全部落到 NVMe = 370s ≈ 6.2min**(c5d 实测,含开机)。✅ 全自动、无需干预。
- 瘦 AMI 快照:仅 OS+docker+vLLM 镜像(vs 烘焙版含 48GB 模型)。
- **端到端 p4de 冷启动实测(2026-07-03,launch → ALB healthy)= 18.7 分钟** ✅
  - 下载到 NVMe:~6.5min;vLLM 加载 + 引擎初始化(CUDA graph + MoE warmup):~11.5min。
  - vs 旧烘焙+冷 EBS 的 ~2 小时/超时崩溃 —— 数量级改进。
  - 想再快:vLLM 加 `--enforce-eager` 可省掉 CUDA graph 捕获那几分钟(代价:运行时吞吐略降)。
