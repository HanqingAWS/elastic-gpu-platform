import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

interface Props extends cdk.StackProps {
  config: EnvironmentConfig;
}

/** 控制平面 VPC + SG。数据平面(GPU 队列)的 VPC 在运行时由 provisioner 动态创建,不在此。 */
export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly albSecurityGroup: ec2.SecurityGroup;
  public readonly serviceSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: Props) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      ipAddresses: ec2.IpAddresses.cidr(props.config.vpcCidr),
      maxAzs: props.config.maxAzs,
      natGateways: 1,
    });

    // 控制平面 ALB(仅承载 Web UI/API)。注:入站按需收紧,不放开公网 0.0.0.0/0 到后端。
    this.albSecurityGroup = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc: this.vpc,
      description: 'NLP-Platform control-plane ALB',
      allowAllOutbound: true,
    });
    // ALB 只允许 CloudFront 回源:引用 CloudFront 托管前缀列表
    // (com.amazonaws.global.cloudfront.origin-facing = pl-3b927c52 @ us-east-1)。绝不 0.0.0.0/0。
    this.albSecurityGroup.addIngressRule(
      ec2.Peer.prefixList('pl-3b927c52'), ec2.Port.tcp(80), 'from CloudFront origin-facing PL');

    // ECS 服务 SG:只接受来自控制平面 ALB 的流量
    this.serviceSecurityGroup = new ec2.SecurityGroup(this, 'ServiceSg', {
      vpc: this.vpc,
      description: 'NLP-Platform control-plane ECS services',
      allowAllOutbound: true,
    });
    this.serviceSecurityGroup.addIngressRule(
      this.albSecurityGroup,
      ec2.Port.tcp(props.config.containerPort),
      'ALB to Web',
    );

    new cdk.CfnOutput(this, 'VpcId', { value: this.vpc.vpcId });
    new cdk.CfnOutput(this, 'ServiceSgId', {
      value: this.serviceSecurityGroup.securityGroupId,
      description: '数据平面节点 metrics 端口应引用此 SG 作为抓取来源',
      exportName: `${id}-service-sg`,
    });
  }
}
