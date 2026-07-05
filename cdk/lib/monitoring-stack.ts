import * as cdk from 'aws-cdk-lib';
import * as cw from 'aws-cdk-lib/aws-cloudwatch';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

interface Props extends cdk.StackProps {
  config: EnvironmentConfig;
  cluster: string;
  tables: Record<string, string>;
}

/** CloudWatch Dashboard + SNS 告警。模式参考 cloud-native-nanoclaw/infra/lib/monitoring-stack.ts。 */
export class MonitoringStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: Props) {
    super(scope, id, props);

    const topic = new sns.Topic(this, 'AlarmsTopic', { topicName: `nlp-${props.config.environmentName}-alarms` });

    const dashboard = new cw.Dashboard(this, 'Dashboard', {
      dashboardName: `nlp-platform-${props.config.environmentName}`,
    });

    dashboard.addWidgets(
      new cw.GraphWidget({
        title: 'ECS 控制平面 CPU/内存',
        left: [
          new cw.Metric({ namespace: 'AWS/ECS', metricName: 'CPUUtilization', dimensionsMap: { ClusterName: props.cluster } }),
          new cw.Metric({ namespace: 'AWS/ECS', metricName: 'MemoryUtilization', dimensionsMap: { ClusterName: props.cluster } }),
        ],
        width: 12,
      }),
      new cw.GraphWidget({
        title: 'DynamoDB 消耗容量',
        left: Object.values(props.tables).map(
          (t) => new cw.Metric({ namespace: 'AWS/DynamoDB', metricName: 'ConsumedReadCapacityUnits', dimensionsMap: { TableName: t } }),
        ),
        width: 12,
      }),
    );

    // 自定义指标占位:各区健康台数 / 目标达成率(由 Agent 服务 PutMetricData 上报,命名空间 NLP-Platform)
    dashboard.addWidgets(
      new cw.GraphWidget({
        title: '各区健康 GPU 台数(自定义)',
        left: props.config.dataPlaneRegions.map(
          (r) => new cw.Metric({ namespace: 'NLP-Platform', metricName: 'HealthyNodes', dimensionsMap: { Region: r } }),
        ),
        width: 24,
      }),
    );

    new cdk.CfnOutput(this, 'AlarmsTopicArn', { value: topic.topicArn });
    new cdk.CfnOutput(this, 'DashboardName', { value: `nlp-platform-${props.config.environmentName}` });
  }
}
