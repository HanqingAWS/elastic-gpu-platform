#!/bin/bash
# ============================================================================
# NLP-Platform 节点 AMI 一键烘焙脚本
# ----------------------------------------------------------------------------
# 在一台由「Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.12 (Amazon Linux 2023)」
# 启动的 GPU 实例上,以 root 跑一次,即安装并 enable 三个 systemd 服务:
#   nlp-fetch-model.service  开机从单一 S3 桶拉模型到本地 NVMe
#   vllm.service             起 vLLM,监听 :SERVING_PORT,暴露 /health + /metrics
#   nlp-metrics-push.service 每 20s 读 /metrics 写控制面 DynamoDB
# 跑完 create-image 即得节点 AMI。模型不烘焙进 AMI(运行时拉),故 baker 实例无需下载模型、
# 也不需要真正跑起 vLLM —— 用便宜的 GPU 实例烘焙即可。
#
# 用法:
#   cp model.env.example model.env && vi model.env   # 至少改 MODEL_BUCKET
#   sudo ./bake-ami.sh
# 或直接用环境变量覆盖:
#   sudo MODEL_BUCKET=nlp-models-044324713311-us-east-1 MODEL_PREFIX=gemma-4-26B-A4B-it ./bake-ami.sh
# ============================================================================
set -euxo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "请用 sudo/root 运行"; exit 1; }

# ---- 配置:命令行 env 优先 → ./model.env → 默认 ----
[ -f "$SRC/model.env" ] && source "$SRC/model.env"
: "${MODEL_BUCKET:?必须设置 MODEL_BUCKET(单一 S3 桶,如 nlp-models-044324713311-us-east-1)}"
MODEL_BUCKET_REGION="${MODEL_BUCKET_REGION:-us-east-1}"
MODEL_PREFIX="${MODEL_PREFIX:-gemma-4-26B-A4B-it}"
SERVING_PORT="${SERVING_PORT:-8000}"
DP="${DP:-4}"; TP="${TP:-2}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"
METRICS_TABLE="${METRICS_TABLE:-nlp-dev-metrics-rollup}"
METRICS_TABLE_REGION="${METRICS_TABLE_REGION:-us-east-1}"
S5CMD_VERSION="${S5CMD_VERSION:-2.2.2}"

# ---- 1) s5cmd(并行 S3 下载器)----
if ! command -v s5cmd >/dev/null 2>&1; then
  curl -fsSL "https://github.com/peak/s5cmd/releases/download/v${S5CMD_VERSION}/s5cmd_${S5CMD_VERSION}_Linux-64bit.tar.gz" \
    | tar xz -C /tmp s5cmd
  install -m755 /tmp/s5cmd /usr/local/bin/s5cmd
fi

# ---- 2) 运行时配置(各服务读取)----
install -d /etc/nlp
cat >/etc/nlp/model.env <<EOF
MODEL_BUCKET=${MODEL_BUCKET}
MODEL_BUCKET_REGION=${MODEL_BUCKET_REGION}
MODEL_PREFIX=${MODEL_PREFIX}
SERVING_PORT=${SERVING_PORT}
NLP_SERVING_PORT=${SERVING_PORT}
DP=${DP}
TP=${TP}
VLLM_IMAGE=${VLLM_IMAGE}
METRICS_TABLE=${METRICS_TABLE}
METRICS_TABLE_REGION=${METRICS_TABLE_REGION}
EOF

# ---- 3) 脚本 + systemd 服务 ----
install -m755 "$SRC/nlp-fetch-model.sh"   /usr/local/bin/nlp-fetch-model.sh
install -m755 "$SRC/nlp-metrics-push.py"  /usr/local/bin/nlp-metrics-push.py
install -m644 "$SRC/nlp-fetch-model.service"  /etc/systemd/system/nlp-fetch-model.service
install -m644 "$SRC/vllm.service"             /etc/systemd/system/vllm.service
install -m644 "$SRC/nlp-metrics-push.service" /etc/systemd/system/nlp-metrics-push.service

# ---- 4) 预拉 vLLM 镜像(烘焙进 AMI,省冷启动首拉)----
docker pull "$VLLM_IMAGE"

# ---- 5) enable(关键:平台 Launch Template 的 userdata 是空标记,一切靠 AMI 自启)----
systemctl daemon-reload
systemctl enable nlp-fetch-model.service vllm.service nlp-metrics-push.service

# ---- 6) 减小快照 ----
sync; fstrim -av / 2>/dev/null || true

set +x
cat <<DONE

============================================================
✅ 烘焙完成。已安装并 enable:nlp-fetch-model / vllm / nlp-metrics-push
   模型不在 AMI 里(运行时从 s3://${MODEL_BUCKET}/${MODEL_PREFIX}/ 拉到本地 NVMe)。

下一步(在你的工作站执行):
  IID=<本 baker 实例 ID>;  REGION=<baker 所在区>
  aws ec2 create-image --instance-id \$IID --name nlp-node-\$(date +%Y%m%d) \\
      --no-reboot --region \$REGION
  # 等 AMI available,把 AMI ID 填进「环境配置向导」→ 该区 → 创建资源。
============================================================
DONE
