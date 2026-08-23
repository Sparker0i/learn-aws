from aws_cdk import (
    # Duration,
    Stack,
    CfnOutput,
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigwv2,
    # aws_sqs as sqs,
)
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct

class AssistantStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # The code that defines your stack goes here

        # example resource
        # queue = sqs.Queue(
        #     self, "InfraQueue",
        #     visibility_timeout=Duration.seconds(300),
        # )
        fn = _lambda.Function(
            self, "AssistantFn",
            runtime=_lambda.Runtime.PYTHON_3_14,
            handler="app.handler",
            code=_lambda.Code.from_asset("../dist")
        )

        api = apigwv2.HttpApi(
            self, "AssistantApi",
            default_integration=HttpLambdaIntegration("AssistantIntegration", fn)
        )

        CfnOutput(self, "ApiUrl", value=api.url)