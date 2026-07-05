import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

interface Props extends cdk.StackProps {
  config: EnvironmentConfig;
}

/** 自助邮箱注册 + 邮箱验证。改编自 sample-bedrock-api-proxy/cdk/lib/cognito-stack.ts,
 *  区别:selfSignUpEnabled=true + autoVerify.email(默认用 Cognito 内置邮件;
 *  量大时可挂 SES,见 cognitoSesFromEmail)。 */
export class CognitoStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: Props) {
    super(scope, id, props);

    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: `nlp-platform-${props.config.environmentName}`,
      selfSignUpEnabled: props.config.cognitoSelfSignup, // 自助注册
      signInAliases: { email: true },
      autoVerify: { email: true },
      userVerification: {
        emailSubject: 'NLP-Platform 邮箱验证码',
        emailBody: '你的验证码是 {####}',
        emailStyle: cognito.VerificationEmailStyle.CODE,
      },
      standardAttributes: { email: { required: true, mutable: true } },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      email: props.config.cognitoSesFromEmail
        ? cognito.UserPoolEmail.withSES({ fromEmail: props.config.cognitoSesFromEmail })
        : undefined,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.userPoolClient = this.userPool.addClient('WebClient', {
      generateSecret: false, // SPA
      authFlows: { userPassword: true, userSrp: true },
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
    });

    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId });
  }
}
