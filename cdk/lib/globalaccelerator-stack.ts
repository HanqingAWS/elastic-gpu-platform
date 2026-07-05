import * as cdk from 'aws-cdk-lib';
import * as ga from 'aws-cdk-lib/aws-globalaccelerator';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

interface Props extends cdk.StackProps {
  config: EnvironmentConfig;
}

/** GA 一次性建好(持有稳定 anycast IP 供客户 DNS 指向)。每区一个 endpoint group,
 *  初始为空;运行时 provisioner 把该区 ALB 注册进去(ClientIPPreservationEnabled=true,
 *  以触发 GA 在数据平面 VPC 自动创建名为 GlobalAccelerator 的 SG),并按健康台数调 TrafficDial。
 *  L1 CfnEndpointGroup 允许无 endpoint 存在。模式参考 bedrock-accelerator/lib/global-accelerator-stack.ts。 */
export class GlobalAcceleratorStack extends cdk.Stack {
  public readonly acceleratorArn: string;

  constructor(scope: Construct, id: string, props: Props) {
    super(scope, id, props);

    const accelerator = new ga.CfnAccelerator(this, 'Accelerator', {
      name: `nlp-platform-${props.config.environmentName}`,
      enabled: true,
      ipAddressType: 'IPV4',
    });
    this.acceleratorArn = accelerator.ref;

    const listener = new ga.CfnListener(this, 'Listener', {
      acceleratorArn: accelerator.ref,
      protocol: 'TCP',
      portRanges: [{ fromPort: 443, toPort: 443 }],
    });

    // 每个数据平面区一个空 endpoint group;endpointConfigurations 运行时由 provisioner 填 ALB。
    props.config.dataPlaneRegions.forEach((region, i) => {
      new ga.CfnEndpointGroup(this, `EndpointGroup${i}`, {
        listenerArn: listener.ref,
        endpointGroupRegion: region,
        // 空;运行时通过 update-endpoint-group 注册 ALB + 设 trafficDialPercentage
        healthCheckPort: 80,
        healthCheckProtocol: 'TCP',
      });
    });

    new cdk.CfnOutput(this, 'AcceleratorArn', { value: accelerator.ref });
    new cdk.CfnOutput(this, 'AcceleratorDnsName', { value: accelerator.attrDnsName });
  }
}
