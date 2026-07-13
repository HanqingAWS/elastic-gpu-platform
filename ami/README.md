# 节点 AMI 烘焙指南

从一台 **Deep Learning 基础 AMI** 起,跑一个脚本(`bake-ami.sh`)即可烘焙出 NLP-Platform 的 GPU 节点 AMI。模型**不烘焙进 AMI**,而是节点开机时从**单一 S3 桶**拉到本地 NVMe(桶名为变量)。

## 这套东西做什么

节点开机顺序(全部 systemd,`enable` 自启):

```
开机 → nlp-fetch-model.service ──(拉完)──> vllm.service ──> nlp-metrics-push.service
        单桶 S3 → 本地 NVMe          vLLM :PORT /health /metrics    每20s 读 /metrics → DynamoDB
```

平台调度节点起来后,靠 `/health` 过 ALB 健康检查、被 GA 路由;性能监控页读 pusher 写入的指标。

## 前置:基础 AMI + 实例

- 基础 AMI:**Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.12 (Amazon Linux 2023)** —— 已含 NVIDIA 驱动 + Docker + nvidia-container-toolkit,烘焙脚本无需再装驱动。
- 起一台该 AMI 的 GPU 实例作 **baker**。烘焙只是装服务 + 预拉 vLLM 镜像,**不下载模型、不真跑 vLLM**,所以用便宜的 GPU 实例即可(不必 p4d)。
- baker 建议给个能访问 GitHub / S3 的角色(拉 s5cmd、`docker pull`);**运行期**的 S3/DynamoDB 权限由平台在 provision 时通过实例角色注入,不用 baker 烘焙凭证。

## 步骤

```bash
# 1) 在 baker 实例上取到本目录(git clone 或 scp ami/ 过来)
cd ami

# 2) 配置(至少改 MODEL_BUCKET)
cp model.env.example model.env
vi model.env          # MODEL_BUCKET / MODEL_PREFIX / SERVING_PORT / DP / TP ...

# 3) 一键烘焙(装 + enable 三个服务,预拉 vLLM 镜像)
sudo ./bake-ami.sh

# 4) 打镜像(在你的工作站)
aws ec2 create-image --instance-id <baker实例ID> --name nlp-node-$(date +%Y%m%d) \
    --no-reboot --region <baker所在区>

# 5) 等 AMI available → 填进「环境配置向导」→ 目标区 → 创建资源
```

## 目录文件

| 文件 | 作用 |
|---|---|
| `bake-ami.sh` | 一键烘焙(装脚本/服务、写 `/etc/nlp/model.env`、预拉镜像、enable) |
| `model.env.example` | 运行时配置模板(桶名/前缀/端口/DP/TP/指标表) |
| `nlp-fetch-model.sh` + `.service` | 开机从**单桶**拉模型到本地 NVMe(`Before=vllm`) |
| `vllm.service` | 起 vLLM(`Requires=nlp-fetch-model`,监听 `SERVING_PORT`,暴露 /health /metrics) |
| `nlp-metrics-push.py` + `.service` | 读 `localhost:SERVING_PORT/metrics` → 写控制面 DynamoDB |

## 单桶设计(变量化)

- 所有区节点都从 `MODEL_BUCKET`(默认 `nlp-models-044324713311-us-east-1`)拉 —— **不再逐区 `nlp-models-*-<region>`**,加区不用再 seed 桶。
- 代价:远区(如 eu-north-1)为**跨区下载**,冷启动的下载阶段会略慢(s5cmd 多连接并行缓解),并有极小跨区流量费(~$0.02/GB × 模型大小)。同区(us-east-2)几乎无差别。
- 想换桶/换区:只改 `model.env` 里 `MODEL_BUCKET` / `MODEL_BUCKET_REGION`,重烤即可(桶名是变量,不写死在脚本里)。

## IAM(平台在 provision 时提供,baker 不用管)

平台的 GPU 节点角色已授:
- `s3:GetObject` on `nlp-models-*`(拉模型)—— 若你的桶名不匹配该通配,需在平台侧策略加上。
- `dynamodb:PutItem` on 指标表(上报)。

节点无需烘焙任何长期密钥;走实例角色。

## 与平台的契约(务必满足)

1. 服务监听端口 = 向导里填的**服务端口**(默认 8000);健康路径 = **健康检查路径**(默认 `/health`)。不一致则 ALB 健康检查过不了、GA 不给流量。
2. 想要性能监控有数据:引擎需暴露 **Prometheus `/metrics`**,且装了 `nlp-metrics-push`。
3. 三个服务必须 `systemctl enable` —— 平台 Launch Template 的 userdata 是空标记,不兜底安装。

## 验证(平台把节点拉起后,在节点上)

```bash
journalctl -u nlp-fetch-model.service   # 下载是否成功(失败会阻断 vLLM)
journalctl -u vllm.service              # 引擎是否就绪(冷启动大模型 ~15-20min)
curl -s localhost:8000/health           # 200 = 健康
curl -s localhost:8000/metrics | head   # vllm:* 计数器
journalctl -u nlp-metrics-push.service  # 推送日志
```
控制面:`aws dynamodb scan --table-name nlp-dev-metrics-rollup --region us-east-1` 看有无新行;或直接看性能监控页。

## 换引擎 / 换模型

- 换模型:S3 放到新前缀,改 `model.env` 的 `MODEL_PREFIX`(和 `MODEL_BUCKET`),按机型调 `DP`/`TP`,重烤。
- 换引擎(TGI / SGLang / Triton 等):改 `vllm.service` 的启动命令;若引擎的 Prometheus 指标名不同,改 `nlp-metrics-push.py` 里 `scrape()` 解析的几个指标名(写库字段格式不变)。

## 踩过的坑(已在脚本里规避)

- **IMDSv2**:读 region 必须先 `PUT /latest/api/token` 取 token,否则读回空。
- **s5cmd 无 `--region`**:只认 `AWS_REGION` 环境变量,传 `--region` 会报 `flag not defined`。
- **冷启动超时**:大模型加载久,vLLM 设 `VLLM_ENGINE_READY_TIMEOUT_S=3600`;pusher 对未就绪静默重试不崩。
- **NVMe**:p4d/p4de 自带实例存储,脚本自动 `mkfs`+挂 `/opt/models`;无 NVMe 则落根盘。
