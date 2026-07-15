// 环境配置。改编自 sample-bedrock-api-proxy/cdk/config/config.ts。
export type EnvName = 'dev' | 'prod';

export interface EnvironmentConfig {
  environmentName: EnvName;
  region: string; // 控制平面部署区(默认 us-east-1)
  // 数据平面目标区(GPU Spot 队列),us-east-1 优先
  dataPlaneRegions: string[];
  // 控制平面网络:默认按 vpcCidr/maxAzs 自建 VPC。
  vpcCidr: string;
  maxAzs: number;
  // BYO 控制面 VPC(可选):填了就用现有 VPC + 指定子网,不自建。
  //   albSubnets    = 公有子网(≥2 个不同 AZ,给 internet-facing ALB)
  //   serviceSubnets = 私有子网(需有出网:NAT 或 ECR/Logs/DDB/Cognito/Bedrock/EC2·ELB·GA 的 VPC endpoint),给 ECS 任务
  controlPlaneVpc?: {
    vpcId: string;
    albSubnets: { subnetId: string; az: string }[];
    serviceSubnets: { subnetId: string; az: string }[];
  };
  // ECS 控制平面服务规格
  webCpu: number;
  webMemory: number;
  agentCpu: number;
  agentMemory: number;
  containerPort: number; // FastAPI
  agentPort: number; // Agent 健康/内部 API
  // 默认调度参数
  defaultTargetCount: number; // 默认目标台数
  // DynamoDB
  dynamodbBillingMode: 'PAY_PER_REQUEST' | 'PROVISIONED';
  // Cognito
  cognitoSelfSignup: boolean;
  cognitoSesFromEmail?: string; // 自助注册验证邮件发件地址(SES 已验证)
  tags: Record<string, string>;
}

const base = {
  region: 'us-east-1',
  dataPlaneRegions: ['us-east-1', 'us-east-2', 'us-west-2', 'eu-north-1'],
  vpcCidr: '10.20.0.0/16',
  maxAzs: 2,
  containerPort: 8000,
  agentPort: 8100,
  defaultTargetCount: 2,
  dynamodbBillingMode: 'PAY_PER_REQUEST' as const,
  cognitoSelfSignup: true,
  tags: { Project: 'NLP-Platform' },
};

export const environments: Record<EnvName, EnvironmentConfig> = {
  dev: {
    ...base,
    environmentName: 'dev',
    webCpu: 512,
    webMemory: 1024,
    agentCpu: 512,
    agentMemory: 1024,
  },
  prod: {
    ...base,
    environmentName: 'prod',
    webCpu: 1024,
    webMemory: 2048,
    agentCpu: 1024,
    agentMemory: 2048,
  },
};

export function getConfig(env: string): EnvironmentConfig {
  const key = (env as EnvName) in environments ? (env as EnvName) : 'dev';
  return environments[key];
}
