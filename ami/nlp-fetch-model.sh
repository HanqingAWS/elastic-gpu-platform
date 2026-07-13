#!/bin/bash
# 开机把模型从「单一 S3 桶」拉到本地 NVMe 实例存储,供 vLLM 使用。
# 桶名/前缀等由 /etc/nlp/model.env 注入(bake-ami.sh 写入)。单桶设计:所有区节点都从
# MODEL_BUCKET(默认在 us-east-1)拉,远区为跨区下载(s5cmd 多连接并行,冷启动会略慢、
# 有极小跨区流量费,换来"加区不用逐区 seed 桶")。
set -x

[ -f /etc/nlp/model.env ] && source /etc/nlp/model.env
: "${MODEL_BUCKET:?必须设置 MODEL_BUCKET(如 nlp-models-044324713311-us-east-1)}"
MODEL_BUCKET_REGION="${MODEL_BUCKET_REGION:-us-east-1}"
MODEL_PREFIX="${MODEL_PREFIX:-gemma-4-26B-A4B-it}"
MODEL_DIR="/opt/models/${MODEL_PREFIX}"

# s5cmd 用桶所在区(不能传 --region;坑:s5cmd 不认 --region,只认 AWS_REGION 环境变量)
export AWS_REGION="${MODEL_BUCKET_REGION}"

# 找本地 NVMe 实例存储挂到 /opt/models(p4d/p4de 自带 8×NVMe;无则用根盘)
NVME=$(lsblk -dpno NAME,MODEL 2>/dev/null | awk '/Instance Storage|EC2 NVMe Instance/{print $1; exit}')
mkdir -p /opt/models
if [ -n "$NVME" ]; then mkfs.ext4 -F "$NVME" && mount "$NVME" /opt/models; fi
mkdir -p "$MODEL_DIR"

# 并行下载(实例角色鉴权,需 s3:GetObject on MODEL_BUCKET)
/usr/local/bin/s5cmd cp "s3://${MODEL_BUCKET}/${MODEL_PREFIX}/*" "${MODEL_DIR}/"
