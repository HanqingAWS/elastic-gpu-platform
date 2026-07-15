#!/usr/bin/env bash
# =============================================================================
# NLP-Platform 控制面一键部署脚本(客户 EC2 / 全新账号)
#
#   用法:  bash deploy.sh [admin-email]
#     admin-email  可选。给了就自动建首个登录用户 + 打印随机密码。
#
#   特性:幂等、可重复运行。自动检查/安装 Node.js、Docker、AWS CLI、CDK;
#         建 ECR、构建推送两镜像、bootstrap、部署 5 个栈、取输出、建用户。
#         尽量不中断:能装的装、能跳的跳,只有真正无法继续才停并给出原因。
#
#   前置:本机(EC2)需有一个带足够权限的 IAM Role 或 aws 凭证
#         (CloudFormation/IAM/ECS/ECR/EC2/Cognito/DynamoDB/CloudFront/
#          GlobalAccelerator/ELB/Logs)。控制面区取 cdk/config/config.ts 的 region。
# =============================================================================
set -u

ADMIN_EMAIL="${1:-}"
NODE_MAJOR=20

# ---- 输出helpers ----
log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m✗ 停止:%s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- 定位仓库根(脚本可放 repo 根)----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if   [ -f "$SCRIPT_DIR/Dockerfile.web" ] && [ -d "$SCRIPT_DIR/cdk" ]; then REPO="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../Dockerfile.web" ] && [ -d "$SCRIPT_DIR/../cdk" ]; then REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
else die "找不到仓库根(需含 Dockerfile.web 与 cdk/)。请把本脚本放在仓库根目录运行。"; fi
cd "$REPO" || die "无法进入 $REPO"
ok "仓库根:$REPO"

# ---- 环境探测 ----
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
PKG=""; for p in dnf yum apt-get; do have "$p" && { PKG="$p"; break; }; done
[ -n "$PKG" ] && ok "包管理器:$PKG" || warn "未识别包管理器,缺组件将只能手动装"
[ "$PKG" = "apt-get" ] && $SUDO apt-get update -y >/dev/null 2>&1

pkg_install() {  # pkg_install <pkg>...
  [ -n "$PKG" ] || return 1
  case "$PKG" in
    dnf|yum) $SUDO "$PKG" install -y "$@" >/dev/null 2>&1 ;;
    apt-get) $SUDO apt-get install -y "$@" >/dev/null 2>&1 ;;
  esac
}

# ---- 控制面区(取自 config.ts,保证 ECR/部署一致)----
CFG_REGION="$(grep -oE "region:[[:space:]]*'[a-z0-9-]+'" "$REPO/cdk/config/config.ts" 2>/dev/null | grep -oE "'[a-z0-9-]+'" | tr -d "'" | head -1)"
REGION="${CFG_REGION:-us-east-1}"
ok "控制面区(来自 config.ts):$REGION"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

# ---- 1) AWS CLI ----
log "检查 AWS CLI"
if ! have aws; then
  warn "未装 AWS CLI,尝试安装 v2"
  have unzip || pkg_install unzip
  if have curl && have unzip; then
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip 2>/dev/null \
      && (cd /tmp && unzip -oq awscliv2.zip && $SUDO ./aws/install --update >/dev/null 2>&1)
  fi
  have aws || pkg_install awscli
fi
have aws || die "AWS CLI 不可用,请手动安装后重试。"
ok "aws $(aws --version 2>&1 | awk '{print $1}')"

# ---- 2) AWS 凭证 / 账号 ----
log "检查 AWS 凭证"
ACCT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"
[ -n "$ACCT" ] && [ "$ACCT" != "None" ] || die "拿不到 AWS 身份。请给该 EC2 附带权限的 IAM Role,或先 aws configure。"
ok "账号:$ACCT  区域:$REGION"
REG_URL="$ACCT.dkr.ecr.$REGION.amazonaws.com"

# ---- 3) Node.js (>=18) ----
log "检查 Node.js"
node_major() { node -v 2>/dev/null | sed 's/v//;s/\..*//'; }
if have node && [ "$(node_major)" -ge 18 ] 2>/dev/null; then
  ok "Node $(node -v)"
else
  warn "无合适 Node,安装 Node.js $NODE_MAJOR(优先 nvm)"
  export NVM_DIR="$HOME/.nvm"
  if [ ! -s "$NVM_DIR/nvm.sh" ] && have curl; then
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash >/dev/null 2>&1 || warn "nvm 安装脚本失败"
  fi
  # shellcheck disable=SC1090
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
  if command -v nvm >/dev/null 2>&1; then
    nvm install "$NODE_MAJOR" >/dev/null 2>&1 && nvm use "$NODE_MAJOR" >/dev/null 2>&1
  fi
  if ! have node; then   # 回退:发行版包
    case "$PKG" in
      dnf|yum) pkg_install nodejs npm ;;
      apt-get) have curl && curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | $SUDO -E bash - >/dev/null 2>&1; pkg_install nodejs ;;
    esac
  fi
  have node || die "Node.js 安装失败。请手动安装 Node >= 18 后重跑本脚本。"
  ok "Node $(node -v)"
fi
have npm || die "npm 不可用(Node 装了但 npm 缺失?)"

# ---- 4) Docker ----
log "检查 Docker"
if ! have docker; then
  warn "未装 Docker,尝试安装"
  case "$PKG" in
    dnf|yum) pkg_install docker ;;
    apt-get) pkg_install docker.io || pkg_install docker ;;
  esac
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || $SUDO service docker start >/dev/null 2>&1 || true
fi
have docker || die "Docker 不可用,请手动安装后重试。"
# 起服务 + 判断是否需要 sudo
$SUDO systemctl start docker >/dev/null 2>&1 || true
DOCKER="docker"; docker ps >/dev/null 2>&1 || DOCKER="$SUDO docker"
$DOCKER ps >/dev/null 2>&1 || die "Docker 守护进程起不来(试试 $SUDO systemctl status docker)。"
ok "Docker 可用($DOCKER)"

# ---- 5) ECR:建仓 + 登录 + 构建 + 推送 ----
log "准备 ECR 仓库(nlp-backend / nlp-agent)"
for r in nlp-backend nlp-agent; do
  if aws ecr describe-repositories --repository-names "$r" --region "$REGION" >/dev/null 2>&1; then
    ok "ECR 已存在:$r"
  else
    aws ecr create-repository --repository-name "$r" --region "$REGION" >/dev/null 2>&1 \
      && ok "ECR 已创建:$r" || die "创建 ECR $r 失败(检查 IAM ecr:CreateRepository 权限)。"
  fi
done

log "登录 ECR"
aws ecr get-login-password --region "$REGION" | $DOCKER login --username AWS --password-stdin "$REG_URL" >/dev/null 2>&1 \
  && ok "ECR 登录成功" || die "ECR 登录失败(检查权限/网络)。"

log "构建并推送 web 镜像 (nlp-backend)"
$DOCKER build -f Dockerfile.web -t "$REG_URL/nlp-backend:latest" . || die "web 镜像构建失败(看上面日志)。"
$DOCKER push "$REG_URL/nlp-backend:latest" >/dev/null || die "web 镜像推送失败。"
ok "nlp-backend:latest 已推送"

log "构建并推送 agent 镜像 (nlp-agent)"
$DOCKER build -t "$REG_URL/nlp-agent:latest" agent/ || die "agent 镜像构建失败(看上面日志)。"
$DOCKER push "$REG_URL/nlp-agent:latest" >/dev/null || die "agent 镜像推送失败。"
ok "nlp-agent:latest 已推送"

# ---- 6) CDK 依赖 ----
log "安装 CDK 依赖 (cdk/)"
( cd "$REPO/cdk" && npm install --no-audit --no-fund >/dev/null 2>&1 ) || die "cdk/ npm install 失败。"
ok "依赖就绪(本地 aws-cdk,使用 npx cdk)"

# ---- 7) CDK bootstrap(幂等)----
log "CDK bootstrap  aws://$ACCT/$REGION"
( cd "$REPO/cdk" && npx cdk bootstrap "aws://$ACCT/$REGION" --require-approval never ) \
  && ok "bootstrap 完成" || warn "bootstrap 返回非 0(通常是已 bootstrap,继续部署)"

# ---- 8) 部署 5 个栈 ----
log "部署控制面 5 个栈(network/dynamodb/cognito/ecs/monitoring)—— 首次约 10-15 分钟"
( cd "$REPO/cdk" && npx cdk deploy --all --require-approval never --context environment=dev ) \
  || die "cdk deploy 失败(看上面 CloudFormation 报错;常见:IAM 权限不足 / 镜像未推 / 区域配额)。"
ok "cdk deploy 完成"

# ---- 9) 取输出 ----
log "读取部署输出"
get_out() { aws cloudformation describe-stacks --stack-name "$1" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null; }
CF="$(get_out nlp-dev-ecs CloudFrontDomain)"
POOL="$(get_out nlp-dev-cognito UserPoolId)"
CLIENT="$(get_out nlp-dev-cognito UserPoolClientId)"
[ -n "$CF" ]   && ok "控制台入口:https://$CF"      || warn "未取到 CloudFront 域名(可稍后 describe-stacks nlp-dev-ecs)"
[ -n "$POOL" ] && ok "Cognito 池:$POOL / client:$CLIENT" || warn "未取到 Cognito 输出"

# ---- 10) 建首个登录用户(可选)----
if [ -n "$ADMIN_EMAIL" ] && [ -n "$POOL" ]; then
  log "创建登录用户 $ADMIN_EMAIL"
  PW="Nlp-$(openssl rand -hex 6 2>/dev/null || date +%s | tail -c 7)-Aa1!"
  aws cognito-idp admin-create-user --user-pool-id "$POOL" --username "$ADMIN_EMAIL" --message-action SUPPRESS \
    --user-attributes Name=email,Value="$ADMIN_EMAIL" Name=email_verified,Value=true --region "$REGION" >/dev/null 2>&1 \
    && ok "用户已创建" || warn "用户可能已存在,尝试直接设密码"
  if aws cognito-idp admin-set-user-password --user-pool-id "$POOL" --username "$ADMIN_EMAIL" \
       --password "$PW" --permanent --region "$REGION" >/dev/null 2>&1; then
    ok "登录用户名:$ADMIN_EMAIL"
    ok "登录密码:  $PW   (请立即登录并修改)"
  else
    warn "设密码失败,请手动:aws cognito-idp admin-set-user-password --user-pool-id $POOL --username $ADMIN_EMAIL --password '<12位强密码>' --permanent --region $REGION"
  fi
elif [ -z "$ADMIN_EMAIL" ]; then
  warn "未传 admin-email,跳过建用户。手动建:"
  printf '      aws cognito-idp admin-create-user --user-pool-id %s --username you@corp.com --message-action SUPPRESS --user-attributes Name=email,Value=you@corp.com Name=email_verified,Value=true --region %s\n' "${POOL:-<POOL>}" "$REGION"
  printf '      aws cognito-idp admin-set-user-password --user-pool-id %s --username you@corp.com --password '"'"'<12位强密码>'"'"' --permanent --region %s\n' "${POOL:-<POOL>}" "$REGION"
fi

# ---- 完成 ----
printf '\n\033[1;32m========================================================\n'
printf '  部署完成 ✅\n'
printf '========================================================\033[0m\n'
[ -n "$CF" ] && printf '  控制台:   https://%s\n' "$CF"
printf '  账号/区:  %s / %s\n' "$ACCT" "$REGION"
printf '\n  后续步骤:\n'
printf '   1) 打开控制台登录(上面的用户名/密码;或按提示手动建用户)。\n'
printf '   2) GA 不由 CDK 创建 —— 进「Global Accelerator」页新建/选择 GA 并「设为默认」。\n'
printf '   3) 进「环境配置向导」:每区选 VPC/子网、BYO 选公网 ALB、填 AMI,点「创建资源」。\n'
printf '   4) 「定时活动」设基础台数或活动窗口 → Agent 到点自动拉起 GPU。\n'
printf '\n  重新运行本脚本是安全的(幂等)。如需换控制面区,先改 cdk/config/config.ts 的 region。\n\n'
