#!/usr/bin/env bash
# =============================================================================
# NLP-Platform 控制面拆除脚本(与 deploy.sh 对应)
#
#   用法:  bash destroy.sh [-y] [--keep-ecr]
#     -y | --yes    跳过确认(非交互/nohup 场景用)
#     --keep-ecr    保留 ECR 仓库 + 镜像(默认会一并删除)
#
#   拆除:CDK 5 个栈(network/dynamodb/cognito/ecs/monitoring)+(默认)ECR 仓库。
#         区域取 cdk/config/config.ts 的 region。CDKToolkit bootstrap 保留(可复用)。
#
#   ⚠️ 数据面(运行时创建的 GPU ASG / BYO ALB / GA / 数据面 VPC)不在 CDK 里,
#      本脚本【不拆】—— 请先在控制台「移除区域」或手动清理,否则继续计费。
# =============================================================================
set -u

YES=0; KEEP_ECR=0
for a in "$@"; do case "$a" in -y|--yes) YES=1 ;; --keep-ecr) KEEP_ECR=1 ;; esac; done

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m✗ 停止:%s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- 定位仓库根 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if   [ -f "$SCRIPT_DIR/Dockerfile.web" ] && [ -d "$SCRIPT_DIR/cdk" ]; then REPO="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../Dockerfile.web" ] && [ -d "$SCRIPT_DIR/../cdk" ]; then REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
else die "找不到仓库根(需含 Dockerfile.web 与 cdk/)。请把脚本放在仓库根运行。"; fi
cd "$REPO" || die "无法进入 $REPO"

# ---- 让 node/npx 可用(deploy.sh 用 nvm 装的,新 shell 需 source;修「cdk/npx 找不到」)----
if ! have node; then
  export NVM_DIR="$HOME/.nvm"
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
  if command -v nvm >/dev/null 2>&1; then
    nvm use 20 >/dev/null 2>&1 || nvm use node >/dev/null 2>&1 || nvm use --lts >/dev/null 2>&1 || true
  fi
fi
have node || die "找不到 node/npx。若 deploy.sh 是用 nvm 装的:先 'source ~/.nvm/nvm.sh'(且用跑 deploy 的同一用户);或装 Node>=18。"
ok "Node $(node -v)"
have npx || die "找不到 npx(Node 装了但 npx 缺?)"

# ---- 区域 + 架构(与 deploy 一致,保证 synth 出的模板匹配)----
CFG_REGION="$(grep -oE "region:[[:space:]]*'[a-z0-9-]+'" "$REPO/cdk/config/config.ts" 2>/dev/null | grep -oE "'[a-z0-9-]+'" | tr -d "'" | head -1)"
REGION="${CFG_REGION:-us-east-1}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
case "$(uname -m)" in aarch64|arm64) CPU_ARCH=ARM64 ;; *) CPU_ARCH=X86_64 ;; esac

# ---- 凭证 ----
ACCT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"
[ -n "$ACCT" ] && [ "$ACCT" != "None" ] || die "拿不到 AWS 身份。请给 EC2 挂有权限的 IAM Role,或 aws configure。"

# ---- 确认 ----
log "将拆除以下内容"
printf '   账号 / 区:  %s / %s\n' "$ACCT" "$REGION"
printf '   CDK 5 栈:   nlp-dev-{network,dynamodb,cognito,ecs,monitoring}\n'
if [ "$KEEP_ECR" = 0 ]; then printf '   ECR 仓库:   nlp-backend, nlp-agent(含镜像)\n'; else printf '   ECR:        保留(--keep-ecr)\n'; fi
warn "数据面(GPU ASG / BYO ALB / GA / 数据面 VPC)不在 CDK 里,本脚本【不拆】——"
warn "请先在控制台「移除区域」或手动清理这些,否则会继续计费。"
if [ "$YES" = 0 ]; then
  printf '\n确认拆除?输入 yes 继续(其它任意键取消):'
  read -r ans
  [ "$ans" = "yes" ] || die "已取消。"
fi

# ---- CDK destroy ----
log "CDK destroy 5 个栈(约 10-20 分钟;CloudFront 删除较慢)"
( cd "$REPO/cdk" && npx cdk destroy --all --force --context environment=dev --context cpuArch="$CPU_ARCH" ) \
  || die "cdk destroy 失败(看上面报错。若卡在依赖:可能有未清的数据面资源 / GA 托管 ENI 还没释放,先清那些再重试)。"
ok "CDK 栈已拆除"

# ---- ECR ----
if [ "$KEEP_ECR" = 0 ]; then
  log "删除 ECR 仓库"
  for r in nlp-backend nlp-agent; do
    aws ecr delete-repository --repository-name "$r" --region "$REGION" --force >/dev/null 2>&1 \
      && ok "已删 ECR $r" || warn "$r 不存在或删除跳过"
  done
fi

printf '\n\033[1;32m========================================================\n'
printf '  拆除完成 ✅\n'
printf '========================================================\033[0m\n'
printf '  已拆:CDK 5 栈%s\n' "$([ "$KEEP_ECR" = 0 ] && echo ' + ECR 仓库' || echo '')"
printf '  保留:CDKToolkit bootstrap(可复用,~几分钱/月;要清需先清空其 S3 暂存桶)\n'
printf '  提醒:数据面(GPU ASG / BYO ALB / GA / 数据面 VPC)请单独在控制台「移除区域」或手动清理。\n\n'
