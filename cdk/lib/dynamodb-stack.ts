import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

interface Props extends cdk.StackProps {
  config: EnvironmentConfig;
}

const S = dynamodb.AttributeType.STRING;
const N = dynamodb.AttributeType.NUMBER;

/** 8 张表,见实施方案数据模型。PAY_PER_REQUEST。 */
export class DynamoDBStack extends cdk.Stack {
  public readonly tables: Record<string, dynamodb.Table> = {};
  public readonly tableNames: Record<string, string> = {};

  constructor(scope: Construct, id: string, props: Props) {
    super(scope, id, props);
    const prefix = `nlp-${props.config.environmentName}`;
    const billingMode =
      props.config.dynamodbBillingMode === 'PROVISIONED'
        ? dynamodb.BillingMode.PROVISIONED
        : dynamodb.BillingMode.PAY_PER_REQUEST;

    const mk = (
      logical: string,
      name: string,
      pk: { n: string; t: dynamodb.AttributeType },
      sk?: { n: string; t: dynamodb.AttributeType },
      ttl?: string,
    ) => {
      const t = new dynamodb.Table(this, logical, {
        tableName: `${prefix}-${name}`,
        partitionKey: { name: pk.n, type: pk.t },
        sortKey: sk ? { name: sk.n, type: sk.t } : undefined,
        billingMode,
        timeToLiveAttribute: ttl,
        removalPolicy: cdk.RemovalPolicy.DESTROY, // 平台元数据,dev 可销毁
        pointInTimeRecovery: props.config.environmentName === 'prod',
      });
      this.tables[logical] = t;
      this.tableNames[logical] = t.tableName;
    };

    mk('Config', 'config', { n: 'config_id', t: S });
    mk('Schedules', 'schedules', { n: 'schedule_id', t: S });
    mk('Runs', 'runs', { n: 'run_id', t: S }, { n: 'phase', t: S });
    mk('FleetState', 'fleet-state', { n: 'region', t: S }, { n: 'asg_kind', t: S });
    mk('InstanceInventory', 'instance-inventory', { n: 'region', t: S }, { n: 'instance_id', t: S }, 'ttl');
    mk('MetricsRollup', 'metrics-rollup', { n: 'instance_id', t: S }, { n: 'ts', t: N }, 'ttl');
    mk('ActionsAudit', 'actions-audit', { n: 'date', t: S }, { n: 'ts_uuid', t: S });
    mk('NetworkSelections', 'network-selections', { n: 'region', t: S });
    // Spot 回收事件(中断预警 / 再平衡 / 被回收终止)+ 监控留存。TTL=90 天(记录写 ttl = now + 90d)。
    mk('SpotEvents', 'spot-events', { n: 'region', t: S }, { n: 'ts', t: N }, 'ttl');
    // 计费/运行时长:每区每天(UTC)累计 spot/od running hours。不设 TTL —— 全部保留,可查历史。
    mk('CostRollup', 'cost-rollup', { n: 'region', t: S }, { n: 'date', t: S });

    new cdk.CfnOutput(this, 'TableNames', { value: JSON.stringify(this.tableNames) });
  }
}
