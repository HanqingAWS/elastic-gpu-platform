#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { getConfig } from '../config/config';
import { NetworkStack } from '../lib/network-stack';
import { DynamoDBStack } from '../lib/dynamodb-stack';
import { CognitoStack } from '../lib/cognito-stack';
import { EcsStack } from '../lib/ecs-stack';
import { GlobalAcceleratorStack } from '../lib/globalaccelerator-stack';
import { MonitoringStack } from '../lib/monitoring-stack';

const app = new cdk.App();
const environment = app.node.tryGetContext('environment') || 'dev';
const config = getConfig(environment);
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: config.region };
const prefix = `nlp-${config.environmentName}`;

const network = new NetworkStack(app, `${prefix}-network`, { env, config });
const data = new DynamoDBStack(app, `${prefix}-dynamodb`, { env, config });
const cognito = new CognitoStack(app, `${prefix}-cognito`, { env, config });
const ga = new GlobalAcceleratorStack(app, `${prefix}-ga`, { env, config });

const ecs = new EcsStack(app, `${prefix}-ecs`, {
  env,
  config,
  vpc: network.vpc,
  albSecurityGroup: network.albSecurityGroup,
  serviceSecurityGroup: network.serviceSecurityGroup,
  tables: data.tables,
  userPool: cognito.userPool,
  userPoolClient: cognito.userPoolClient,
  acceleratorArn: ga.acceleratorArn,
});
ecs.addDependency(network);
ecs.addDependency(data);
ecs.addDependency(cognito);
ecs.addDependency(ga);

new MonitoringStack(app, `${prefix}-monitoring`, {
  env,
  config,
  cluster: ecs.clusterName,
  tables: data.tableNames,
});

for (const s of [network, data, cognito, ga, ecs]) {
  Object.entries(config.tags).forEach(([k, v]) => cdk.Tags.of(s).add(k, v));
}
app.synth();
