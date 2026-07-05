# 冷启动优化:瘦 AMI + S3 模型 + 开机下载到本地 NVMe

> 设计文档 · 2026-07-03 · NLP-Platform 数据平面 GPU 节点镜像

## 1. 背景与问题

数据平面的 GPU 节点(p4de/p4d,8×A100)从一个**把模型权重烘焙进去**的 AMI 启动。
Gemma-4-26B-A4B 权重约 **48 GB**(2 个 safetensors ×24GB + 分词器等)。

**实测问题**:全新实例首次启动,vLLM 加载权重极慢,**约 2 小时**才能对外服务;
更糟的是常常在 vLLM 默认 600s(后调到 3600s)引擎就绪超时前都加载不完,进而 **超时崩溃**、
`Restart=always` 反复重启才勉强起来。

### 1.1 根因:EBS 快照的懒加载(lazy-load)水合限速

AMI 的根卷是一个存放在 S3 的 **EBS 快照**。新实例从快照创建卷时,数据**并不预先拷贝**——
块设备是"假的满",每个块**第一次被读**时才从 S3 拉取(first-touch),且 AWS 对这个
初始水合速率**限速**。

- 实测冷卷读取速率 **~4–5 MB/s**(远低于该 gp3 卷本身 125 MB/s 的能力,只用了约 1/25)。
- 48 GB ÷ 5 MB/s ≈ **2.7 小时**。
- 这是**每台新实例首次启动**的一次性代价;同一台重启因块已"读热"而快。

### 1.2 为什么换 io2 / 加吞吐没用

瓶颈是**快照首次水合的固有限速**,不是卷的性能等级:

- 冷卷只跑 ~5 MB/s,而 gp3 已提供 125 MB/s、3000 IOPS —— **连现有天花板都远没碰到**。
- 换 io2 / 提高 gp3 吞吐只是抬高天花板,对"水合限速"无效。

### 1.3 可选根治手段对比

| 手段 | 冷启动 | 成本 | 结论 |
|---|---|---|---|
| 烘焙模型 + 默认 | ~2 小时 / 超时崩 | AMI 大 | ❌ 不可用 |
| 烘焙模型 + EBS FSR | ~2–5 min | FSR 按 AZ×快照 计费(~$0.75/AZ/h) | ✅ 但持续付费 |
| **瘦 AMI + S3 + 开机下载到 NVMe** | **~10–12 min** | 仅 S3 存储(~$1/月/区) | ✅ **本方案** |

## 2. 方案:瘦 AMI + S3 seeding + 开机下载

**核心思想**:AMI 里**不含**模型(瘦 → 快照小 → 恢复快);模型放**同区 S3**;
实例开机时用 `s5cmd` **并行下载**到**本地 NVMe 实例存储**,再起 vLLM。
主动下载(顺序大文件、高并发)不受快照水合限速,远快于 lazy-load。

### 2.1 架构

```
                每个 region 一份:
   ┌─────────────────────────────┐        ┌──────────────────────────────┐
   │ S3: nlp-models-<acct>-<rgn>  │        │ 瘦 AMI (无模型, ~15-20GB)     │
   │   gemma-4-26B-A4B-it/*.safe… │        │  · docker + vllm/vllm-openai  │
   └──────────────┬──────────────┘        │  · s5cmd                      │
                  │                        │  · nlp-fetch-model.service    │
                  │  开机 s5cmd 并行下载     │  · vllm.service (After=fetch) │
                  ▼                        └───────────────┬──────────────┘
   ┌─────────────────────────────┐                         │ 启动实例
   │ 本地 NVMe 实例存储 (临时盘)   │◀────────────────────────┘
   │   /opt/models/gemma-…-it/    │        vLLM 从 NVMe 读(本地 SSD ~GB/s,不再 lazy-load)
   └─────────────────────────────┘
```

### 2.2 开机流程(systemd 顺序)

`cloud-init` → **`nlp-fetch-model.service`**(oneshot,`Before=vllm.service`)→ **`vllm.service`**

`nlp-fetch-model.service` 执行 `/usr/local/bin/nlp-fetch-model.sh`:

```bash
#!/bin/bash
set -x
MODEL_DIR=/opt/models/gemma-4-26B-A4B-it
# IMDSv2:必须先取 token,否则 region 读回空
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
export AWS_REGION="$REGION"                 # s5cmd 用 AWS_REGION,而非 --region
BUCKET=nlp-models-044324713311-$REGION
# 找本地 NVMe 实例存储并挂到 /opt/models(p4de/p4d 自带 8×NVMe)
NVME=$(lsblk -dpno NAME,MODEL 2>/dev/null | awk '/Instance Storage|EC2 NVMe Instance/{print $1; exit}')
mkdir -p /opt/models
if [ -n "$NVME" ]; then mkfs.ext4 -F "$NVME" && mount "$NVME" /opt/models; fi
mkdir -p "$MODEL_DIR"
/usr/local/bin/s5cmd cp "s3://$BUCKET/gemma-4-26B-A4B-it/*" "$MODEL_DIR/"
```

`vllm.service`(不变,仍挂 `/opt/models`)追加 drop-in `10-after-fetch.conf`:

```ini
[Unit]
After=nlp-fetch-model.service
Requires=nlp-fetch-model.service
```

## 3. 实测数据

| 阶段 | 旧(烘焙+冷EBS) | 新(瘦AMI+S3+NVMe) |
|---|---|---|
| 权重读取速率 | ~5 MB/s(快照懒加载限速) | **~147 MB/s**(实测) |
| 启动 → 48GB 到本地 NVMe | ~2.7 小时 | **~6.5 分钟**(p4de 实测:00:19:46→00:26:17) |
| vLLM 从 NVMe 加载 + 引擎初始化(CUDA graph + MoE warmup) | (混在 lazy-load 里) | **~11.5 分钟**(p4de 实测:00:26:17→00:37:46) |
| **端到端冷启动(launch → ALB healthy)** | **~2 小时 / 超时崩溃** | **≈ 18.7 分钟**(p4de.24xlarge 实测 ✅) |
| AMI 快照 | 48GB+ | ~15–20GB(仅 OS+docker+vLLM 镜像) |

> **p4de 端到端实测(2026-07-03)**:实例 `i-0aea…` launch 00:19:46 → ALB healthy,冷启动 **18.7min**。
> 其中下载(~6.5min)已达预期;**vLLM 引擎初始化占 ~11.5min**(48GB 灌 8×GPU + CUDA graph 捕获 + MoE warmup),
> 这是 vLLM 自身启动开销、与下载无关(旧烘焙 AMI 也有,只是被 2h 冷 EBS 掩盖)。
> 想再压缩这段:vLLM 加 `--enforce-eager`(跳过 CUDA graph 捕获,启动快几分钟,代价是运行时吞吐略降)。

## 4. 组件与资源

- **S3(每区一桶)**:`nlp-models-<account>-<region>`,内含 `gemma-4-26B-A4B-it/`。
- **瘦 AMI(每区一个)**:`docker` + 预拉 `vllm/vllm-openai` 镜像 + `s5cmd` +
  `nlp-fetch-model.service`(enabled)+ `vllm.service` drop-in。
- **本地 NVMe**:p4de/p4d 自带实例存储(临时盘,停机即失);模型每次开机重下(~5min,可接受)。
- **IAM**:GPU 节点角色需 `s3:GetObject`/`s3:ListBucket`(下载),seeding 时需 `s3:PutObject`。
  已加内联策略 `nlp-models-s3`,资源限定 `arn:aws:s3:::nlp-models-044324713311-*`。

## 5. 关键坑(踩过的)

1. **IMDSv2**:实例默认 `HttpTokens=required`,`curl http://169.254.169.254/...`(IMDSv1)
   读 region 返回**空** → bucket 名缺 region。必须先 `PUT /latest/api/token` 取 token。
2. **s5cmd 无 `--region`**:会报 `flag provided but not defined: -region`。改用 `AWS_REGION` 环境变量。
3. **瘦快照要 `fstrim`**:`rm` 掉烘焙模型后要 `fstrim -av /` 释放块,快照才真的小。
4. **NVMe 是临时盘**:停机/回收即丢;适合"每晚拉起即用"的临时 GPU 队列,不适合需持久化的场景。
5. **create-image `--no-reboot`**:模型在临时 NVMe 上,不会进快照;瘦 AMI 只含 EBS 根盘。

## 6. 三区落地步骤(seeding + AMI + LT/Config)

对每个 region(us-east-1 / us-east-2 / us-west-2):

1. 建桶 `nlp-models-<acct>-<region>`,把 `gemma-4-26B-A4B-it/` seed 进去(一次性)。
   - 首次可用一台便宜实例从旧 AMI 抽取上传;或跨区 `s3 sync`/`s5cmd cp` 从已 seed 的区复制。
2. 把瘦 AMI 复制到该区(`copy-image`)。
3. 更新该区 Launch Template `$Latest` 的 `ImageId` 为瘦 AMI;`Config.regions[<region>].ami_arn` 同步。
4. GPU 节点角色确保有该区桶的 S3 读权限(通配 `nlp-models-<acct>-*` 已覆盖)。

## 7. 运维要点

- **成本**:平时只有 S3 存储费(~$1/月/区);GPU 只在活动窗口拉起。
- **换模型**:只改 S3 内容(或加新前缀 + 改 fetch 脚本/Config),**无需重烘焙 AMI**。
- **下载失败**:`nlp-fetch-model.service` 失败会阻止 vLLM(`Requires=`),日志见
  `journalctl -u nlp-fetch-model.service`;常见为 IAM/网络/桶名。
- **可选加速**:`s5cmd` 提高并发(`--numworkers`),或换更大网络机型;仍嫌慢可叠加 FSR。
