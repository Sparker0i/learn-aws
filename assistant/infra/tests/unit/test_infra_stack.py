import aws_cdk as core
import aws_cdk.assertions as assertions

from infra.assistant_stack import AssistantStack


def test_lambda_function_created():
    app = core.App()
    stack = AssistantStack(app, "AssistantStack")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties("AWS::Lambda::Function", {
        "Runtime": "python3.14",
    })
