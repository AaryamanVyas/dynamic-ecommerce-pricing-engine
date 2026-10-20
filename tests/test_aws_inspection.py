"""Read-only deployment inspection at its AWS client and unsigned HTTP seams."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from aws_inspection import inspect_deployment, console_url


class InspectionTests(unittest.TestCase):
    def test_reports_actual_resources_and_partial_failures_without_claiming_health(self):
        session = Mock(region_name="ap-south-1")
        cf, sts, gateway, db, ses = [Mock() for _ in range(5)]
        session.client.side_effect = lambda name, **kwargs: {"cloudformation": cf, "sts": sts, "apigatewayv2": gateway,
                                                            "dynamodb": db, "sesv2": ses}[name]
        cf.describe_stacks.return_value = {"Stacks": [{"StackName": "test", "StackId": "arn:aws:cloudformation:ap-south-1:123456789012:stack/test/id",
                                                       "StackStatus": "UPDATE_COMPLETE", "Outputs": [{"OutputKey": "PriceMode", "OutputValue": "mock"}, {"OutputKey":"EventBusName", "OutputValue":"test-prices"}],
                                                       "Parameters": [{"ParameterKey": "EnableSchedule", "ParameterValue": "false"}]}]}
        cf.list_stack_resources.side_effect = [
            {"StackResourceSummaries": [{"LogicalResourceId": "Api", "PhysicalResourceId": "abc123", "ResourceType": "AWS::ApiGatewayV2::Api", "ResourceStatus": "CREATE_COMPLETE"}], "NextToken": "next"},
            {"StackResourceSummaries": [{"LogicalResourceId": "PricesTable", "PhysicalResourceId": "real-prices", "ResourceType": "AWS::DynamoDB::Table", "ResourceStatus": "CREATE_COMPLETE"}]}]
        sts.get_caller_identity.return_value = {"Account": "123456789012"}
        gateway.get_routes.return_value = {"Items": [{"RouteKey": "POST /checks", "AuthorizationType": "AWS_IAM", "Target":"integrations/evb"}, {"RouteKey":"GET /products", "AuthorizationType":"AWS_IAM"}]}
        gateway.get_integrations.return_value = {"Items": [{"IntegrationId":"evb", "IntegrationSubtype": "EventBridge-PutEvents", "RequestParameters":{"EventBusName":"test-prices"}}]}
        db.describe_table.return_value = {"Table": {"TableStatus": "ACTIVE", "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"}}}
        db.describe_time_to_live.return_value = {"TimeToLiveDescription": {"TimeToLiveStatus": "ENABLED", "AttributeName": "expiresAt"}}
        ses.get_email_identity.side_effect = RuntimeError("denied")
        ses.get_account.side_effect = RuntimeError("denied")
        report = inspect_deployment(session, "test", "demo@example.com", probe=lambda url: 403)
        checks = {row["id"]: row for row in report["checks"]}
        self.assertEqual(report["accountId"], "123456789012")
        self.assertEqual(len(report["resources"]), 2)
        self.assertIn("real-prices", report["resources"][1]["consoleUrl"])
        self.assertEqual(checks["api-auth"]["status"], "pass")
        self.assertEqual(checks["unsigned-api"]["status"], "pass")
        self.assertEqual(checks["ses-identity"]["status"], "unknown")
        self.assertEqual(checks["history-ttl"]["status"], "pass")
        self.assertTrue(report["errors"])
        self.assertEqual(cf.list_stack_resources.call_count, 2)
        self.assertEqual(checks["api-integration"]["status"], "pass")
        cf.list_stack_resources.side_effect = None
        cf.list_stack_resources.return_value = {"StackResourceSummaries":[{"LogicalResourceId":"Api", "PhysicalResourceId":"abc123", "ResourceType":"AWS::ApiGatewayV2::Api", "ResourceStatus":"CREATE_COMPLETE"}]}
        gateway.get_integrations.return_value["Items"][0]["IntegrationId"] = "orphaned"
        report = inspect_deployment(session,"test","demo@example.com",probe=lambda url:403)
        self.assertEqual(next(row for row in report["checks"] if row["id"]=="api-integration")["status"],"fail")

    def test_log_console_link_encodes_nested_group_names(self):
        self.assertIn("$252Faws$252Flambda$252Fcollector", console_url("ap-south-1","AWS::Logs::LogGroup","/aws/lambda/collector"))


if __name__ == "__main__":
    unittest.main()
