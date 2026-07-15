import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

interface Props extends cdk.StackProps {
  config: EnvironmentConfig;
  vpc: ec2.IVpc;
  albSubnets: ec2.ISubnet[];       // 公有,ALB 用
  serviceSubnets: ec2.ISubnet[];   // 私有(需出网),ECS 任务用
  albSecurityGroup: ec2.ISecurityGroup;
  serviceSecurityGroup: ec2.ISecurityGroup;
  tables: Record<string, dynamodb.Table>;
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
  acceleratorArn?: string;   // GA 不再由 CDK 创建;空=手动/运行时建 GA 后界面选择、存 Config
}

export class EcsStack extends cdk.Stack {
  public readonly clusterName: string;

  constructor(scope: Construct, id: string, props: Props) {
    super(scope, id, props);
    const c = props.config;
    // CPU 架构:从 context 读(deploy.sh 按构建机 uname -m 传);默认 X86_64。
    // ARM64 用于 t4g/Graviton 构建机 —— 原生构建 arm64 镜像 + 更省的 Fargate;须与推送镜像架构一致。
    const cpuArch = this.node.tryGetContext('cpuArch') === 'ARM64'
      ? ecs.CpuArchitecture.ARM64 : ecs.CpuArchitecture.X86_64;
    // 容器镜像:从 context 读 ECR URI(无需本地 Docker);默认指向本账号 ECR nlp-backend/nlp-agent:latest
    const account = cdk.Stack.of(this).account;
    const backendImage = this.node.tryGetContext('backendImageUri')
      || `${account}.dkr.ecr.${c.region}.amazonaws.com/nlp-backend:latest`;
    const agentImage = this.node.tryGetContext('agentImageUri')
      || `${account}.dkr.ecr.${c.region}.amazonaws.com/nlp-agent:latest`;

    const cluster = new ecs.Cluster(this, 'Cluster', {
      clusterName: `nlp-${c.environmentName}`,
      vpc: props.vpc,
      containerInsights: true,
    });
    this.clusterName = cluster.clusterName;

    // ---- GPU 节点实例角色(数据平面实例用;经 iam:PassRole 由 provisioner 挂到 Launch Template) ----
    const gpuNodeRole = new iam.Role(this, 'GpuNodeRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        // 免密钥登录(SSM Session Manager);私有子网需 NAT 或 SSM VPC endpoint 才可达
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchAgentServerPolicy'),
      ],
    });
    // 节点运行时需要:从 S3 拉模型 + 向控制面 DynamoDB 推指标(此前仅线上手动加,现纳入 CDK)
    gpuNodeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:ListBucket'],
      resources: ['arn:aws:s3:::nlp-models-*', 'arn:aws:s3:::nlp-models-*/*'],
    }));
    gpuNodeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['dynamodb:PutItem'],
      resources: [`arn:aws:dynamodb:*:${cdk.Stack.of(this).account}:table/*-metrics-rollup`],
    }));
    const gpuNodeProfile = new iam.CfnInstanceProfile(this, 'GpuNodeProfile', {
      roles: [gpuNodeRole.roleName],
    });

    // ---- 控制平面 task role:调 AWS API 做 provisioning + 调度 ----
    const taskRole = new iam.Role(this, 'TaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });
    // EC2 / ASG / ELB / GA:这些 API 难做资源级限定,限定到动作层
    taskRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        'ec2:Describe*', 'ec2:CreateVpc', 'ec2:CreateSubnet', 'ec2:CreateSecurityGroup',
        'ec2:ModifyVpcAttribute', 'ec2:DeleteVpc', 'ec2:DeleteSubnet', 'ec2:DeleteSecurityGroup',
        'ec2:AuthorizeSecurityGroupIngress', 'ec2:RevokeSecurityGroupIngress', 'ec2:CreateTags',
        'ec2:CreateKeyPair', 'ec2:DeleteKeyPair', 'ec2:CreateLaunchTemplate', 'ec2:DeleteLaunchTemplate',
        'ec2:CreateLaunchTemplateVersion', 'ec2:ModifyLaunchTemplate', 'ec2:DeleteLaunchTemplateVersion',
        'ec2:RunInstances', 'ec2:TerminateInstances', 'ec2:CreateInternetGateway', 'ec2:AttachInternetGateway',
        'ec2:DetachInternetGateway', 'ec2:DeleteInternetGateway', 'ec2:DeleteRouteTable',
        'ec2:CreateRouteTable', 'ec2:CreateRoute', 'ec2:AssociateRouteTable', 'ec2:DisassociateRouteTable',
        'ec2:ModifySubnetAttribute',
        'ec2:GetSpotPlacementScores', 'ec2:RequestSpotInstances', 'ec2:CancelSpotInstanceRequests',
        'autoscaling:*', 'elasticloadbalancing:*', 'globalaccelerator:*',
        'servicequotas:GetServiceQuota',
      ],
      resources: ['*'],
    }));
    // 只允许把 GPU 节点角色 PassRole 给 ec2/autoscaling
    taskRole.addToPolicy(new iam.PolicyStatement({
      actions: ['iam:PassRole'],
      resources: [gpuNodeRole.roleArn],
      conditions: { StringEquals: { 'iam:PassedToService': ['ec2.amazonaws.com', 'autoscaling.amazonaws.com'] } },
    }));
    taskRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['*'],
    }));
    Object.values(props.tables).forEach((t) => t.grantReadWriteData(taskRole));

    const execRole = new iam.Role(this, 'ExecRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy')],
    });

    const commonEnv: Record<string, string> = {
      AWS_REGION: c.region,
      ENVIRONMENT: c.environmentName,
      DATA_PLANE_REGIONS: c.dataPlaneRegions.join(','),
      DEFAULT_TARGET_COUNT: String(c.defaultTargetCount),
      COGNITO_USER_POOL_ID: props.userPool.userPoolId,
      COGNITO_CLIENT_ID: props.userPoolClient.userPoolClientId,
      COGNITO_REGION: c.region,
      GA_ACCELERATOR_ARN: props.acceleratorArn || '',   // 空=无 CDK GA;运行时选的 GA 存 Config
      GPU_NODE_INSTANCE_PROFILE_ARN: gpuNodeProfile.attrArn,
      GPU_NODE_ROLE_ARN: gpuNodeRole.roleArn,
      CONTROL_PLANE_SG_ID: props.serviceSecurityGroup.securityGroupId,
      ...Object.fromEntries(Object.entries(props.tables).map(([k, t]) => [`TABLE_${k.toUpperCase()}`, t.tableName])),
    };

    // ---- Web 服务(React+FastAPI)behind ALB ----
    const webLog = new logs.LogGroup(this, 'WebLog', { retention: logs.RetentionDays.TWO_WEEKS, removalPolicy: cdk.RemovalPolicy.DESTROY });
    const webTask = new ecs.FargateTaskDefinition(this, 'WebTask', { cpu: c.webCpu, memoryLimitMiB: c.webMemory, taskRole, executionRole: execRole, runtimePlatform: { cpuArchitecture: cpuArch } });
    webTask.addContainer('web', {
      image: ecs.ContainerImage.fromRegistry(backendImage),
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'web', logGroup: webLog }),
      environment: commonEnv,
      portMappings: [{ containerPort: c.containerPort }],
    });
    const webService = new ecs.FargateService(this, 'WebService', {
      cluster, taskDefinition: webTask, desiredCount: 1,
      securityGroups: [props.serviceSecurityGroup as ec2.SecurityGroup],
      vpcSubnets: { subnets: props.serviceSubnets },
      assignPublicIp: false,
    });

    const alb = new elbv2.ApplicationLoadBalancer(this, 'Alb', {
      vpc: props.vpc, internetFacing: true, securityGroup: props.albSecurityGroup as ec2.SecurityGroup,
      vpcSubnets: { subnets: props.albSubnets },
      loadBalancerName: 'nlp-dev-cp-alb',  // 固定名(此前控制面 ALB 被带外删除;显式命名以触发 CFN 重建并稳定 DNS)
    });
    const listener = alb.addListener('Http', { port: 80, protocol: elbv2.ApplicationProtocol.HTTP, open: false });
    listener.addTargets('WebTg', {
      port: c.containerPort, protocol: elbv2.ApplicationProtocol.HTTP, targets: [webService],
      healthCheck: { path: '/health', interval: cdk.Duration.seconds(30), healthyThresholdCount: 2 },
    });

    // CloudFront 在前:提供 HTTPS + 全球加速;回源走 ALB HTTP:80(ALB SG 只放行 CloudFront PL)。
    const dist = new cloudfront.Distribution(this, 'Cdn', {
      comment: 'NLP-Platform UI/API',
      defaultBehavior: {
        origin: new origins.LoadBalancerV2Origin(alb, {
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY, httpPort: 80,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,               // 动态应用不缓存
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,                // 支持 API POST 等
      },
    });
    new cdk.CfnOutput(this, 'CloudFrontDomain', { value: dist.distributionDomainName });

    // ---- Agent/Scheduler 服务(无对外端口,常驻控制循环) ----
    const agentLog = new logs.LogGroup(this, 'AgentLog', { retention: logs.RetentionDays.TWO_WEEKS, removalPolicy: cdk.RemovalPolicy.DESTROY });
    const agentTask = new ecs.FargateTaskDefinition(this, 'AgentTask', { cpu: c.agentCpu, memoryLimitMiB: c.agentMemory, taskRole, executionRole: execRole, runtimePlatform: { cpuArchitecture: cpuArch } });
    agentTask.addContainer('agent', {
      image: ecs.ContainerImage.fromRegistry(agentImage),
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'agent', logGroup: agentLog }),
      environment: commonEnv,
      portMappings: [{ containerPort: c.agentPort }],
    });
    new ecs.FargateService(this, 'AgentService', {
      cluster, taskDefinition: agentTask, desiredCount: 1,
      securityGroups: [props.serviceSecurityGroup as ec2.SecurityGroup],
      vpcSubnets: { subnets: props.serviceSubnets }, assignPublicIp: false,
    });

    new cdk.CfnOutput(this, 'AlbDnsName', { value: alb.loadBalancerDnsName });
    new cdk.CfnOutput(this, 'GpuNodeInstanceProfileArn', { value: gpuNodeProfile.attrArn });
  }
}
