# NLP-Platform

多区域 GPU **Spot** 推理调度管理平台 —— 晚高峰对小型(MoE)LLM 实时短调用做削峰。

- **机型**:p4de.24xlarge 优先、p4d.24xlarge 兜底(Spot);Spot 抢不满时 On-Demand ASG 兜底。
- **区域**:us-east-1(优先)、us-east-2、us-west-2。
- **统一入口**:AWS Global Accelerator(anycast IP),按各区健康台数动态配权。
- **模型**:客户自打包 AMI,平台只填 AMI ARN(model-agnostic)。
- **单账号**:控制平面用 ECS task role 调 AWS API,无 AK/SK。

## 架构(两平面)

- **控制平面**(CDK 一次性建,us-east-1):ECS Fargate 双服务(Web:React+FastAPI / Agent:Strands)、DynamoDB、Cognito(自助邮箱注册)、Global Accelerator、CloudWatch/SNS。
- **数据平面**(运行时 boto3 动态建、每晚销毁,3 区):VPC/子网、SG(引用 GA 自动创建的 `GlobalAccelerator` SG,无 0.0.0.0/0)、Launch Templates(p4de/p4d)、6 个 ASG(3 区 ×{Spot,OD})、区域 ALB(GA endpoint)。

## 目录

```
cdk/        AWS CDK (TypeScript) — 控制平面 6 个栈
backend/    FastAPI 后端(Web 服务)
agent/      Strands Agent / Scheduler 服务
frontend/   React + Vite + Amplify Web UI
```

## 部署

```bash
cd cdk && npm install && npx cdk deploy --all --context environment=dev   # 默认 us-east-1
```

详见实施方案:`~/.claude/plans/`。分阶段 P0→P5,当前:P0(骨架 + 鉴权)。
