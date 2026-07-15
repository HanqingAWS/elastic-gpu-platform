#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { getConfig } from '../config/config';
import { NetworkStack } from '../lib/network-stack';
import { DynamoDBStack } from '../lib/dynamodb-stack';
import { CognitoStack } from '../lib/cognito-stack';
import { EcsStack } from '../lib/ecs-stack';
import { MonitoringStack } from '../lib/monitoring-stack';
// GA 不再由 CDK 创建(改为手动/运行时建 + 界面选择);globalaccelerator-stack 保留在库里但不实例化。

const app = new cdk.App();
const environment = app.node.tryGetContext('environment') || 'dev';
const config = getConfig(environment);
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: config.region };
const prefix = `nlp-${config.environmentName}`;

const network = new NetworkStack(app, `${prefix}-network`, { env, config });
const data = new DynamoDBStack(app, `${prefix}-dynamodb`, { env, config });
const cognito = new CognitoStack(app, `${prefix}-cognito`, { env, config });

const ecs = new EcsStack(app, `${prefix}-ecs`, {
  env,
  config,
  vpc: network.vpc,
  albSecurityGroup: network.albSecurityGroup,
  serviceSecurityGroup: network.serviceSecurityGroup,
  tables: data.tables,
  userPool: cognito.userPool,
  userPoolClient: cognito.userPoolClient,
  // GA 不由 CDK 创建 —— 手动/运行时建 GA,界面选择后存 Config;agent 读 Config 的 GA ARN
});
ecs.addDependency(network);
ecs.addDependency(data);
ecs.addDependency(cognito);

new MonitoringStack(app, `${prefix}-monitoring`, {
  env,
  config,
  cluster: ecs.clusterName,
  tables: data.tableNames,
});

for (const s of [network, data, cognito, ecs]) {
  Object.entries(config.tags).forEach(([k, v]) => cdk.Tags.of(s).add(k, v));
}
app.synth();
