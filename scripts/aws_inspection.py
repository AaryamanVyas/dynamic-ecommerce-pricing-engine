"""Read-only AWS evidence, from the signed-in account rather than a static diagram."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from botocore.config import Config

ROLES = {
    "AWS::ApiGatewayV2::Api": ("API Gateway", "IAM-authenticated entry point"),
    "AWS::Events::EventBus": ("EventBridge", "Decouples request and price-observation events"),
    "AWS::Events::Rule": ("EventBridge", "Routes matching events or schedules hourly collection"),
    "AWS::Lambda::Function": ("Lambda", "Runs catalog APIs, collection or price evaluation"),
    "AWS::DynamoDB::Table": ("DynamoDB", "Persists product rules or dated observations"),
    "AWS::IAM::Role": ("IAM", "Service execution permissions; inspect policies in AWS"),
    "AWS::Logs::LogGroup": ("CloudWatch", "Function logs and correlated observation IDs"),
    "AWS::CloudWatch::Alarm": ("CloudWatch", "Detects messages waiting in the failure queue"),
    "AWS::SQS::Queue": ("SQS", "Retains failed asynchronous work for inspection"),
}


def console_url(region, resource_type, name):
    base = f"https://{region}.console.aws.amazon.com"
    encoded = quote(name, safe="")
    log_encoded = quote(quote(name, safe=""), safe="").replace("%", "$")
    paths = {
        "AWS::ApiGatewayV2::Api": f"/apigateway/main/apis/{encoded}/routes?region={region}",
        "AWS::Lambda::Function": f"/lambda/home?region={region}#/functions/{encoded}",
        "AWS::DynamoDB::Table": f"/dynamodbv2/home?region={region}#table?name={encoded}",
        "AWS::Events::EventBus": f"/events/home?region={region}#/eventbuses/{encoded}",
        "AWS::Events::Rule": f"/events/home?region={region}#/rules",
        "AWS::Logs::LogGroup": f"/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{log_encoded}",
        "AWS::CloudWatch::Alarm": f"/cloudwatch/home?region={region}#alarmsV2:alarm/{encoded}",
        "AWS::SQS::Queue": f"/sqs/v3/home?region={region}#/queues/{encoded}",
        "AWS::IAM::Role": f"/iam/home#/roles/details/{encoded}",
    }
    return base + paths.get(resource_type, f"/cloudformation/home?region={region}")


def _probe(url):
    try:
        with urlopen(url, timeout=8) as response:
            return response.status
    except HTTPError as error:
        return error.code


def _pages(client, method, key, **kwargs):
    rows = []
    while True:
        result = getattr(client, method)(**kwargs)
        rows.extend(result.get(key, []))
        if not result.get("NextToken"):
            return rows
        kwargs["NextToken"] = result["NextToken"]


def inspect_deployment(session, stack_name, email, probe=_probe):
    """Inspect only the named stack and its resources; no deployment or secret reads.

    Failure of the base stack lookup is fatal. Optional checks fail independently;
    unknown never means healthy, and old reports keep their inspection timestamp.
    """
    region = session.region_name
    config = Config(connect_timeout=4, read_timeout=8, retries={"max_attempts": 1})
    client = lambda name: session.client(name, config=config)
    cf = client("cloudformation")
    stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    raw = _pages(cf, "list_stack_resources", "StackResourceSummaries", StackName=stack_name)
    parameters = {row["ParameterKey"]: row.get("ParameterValue") for row in stack.get("Parameters", [])}
    outputs = {row["OutputKey"]: row["OutputValue"] for row in stack.get("Outputs", [])}
    report = {"schemaVersion": 1, "inspectedAt": datetime.now(timezone.utc).isoformat(), "region": region,
              "stack": {"name": stack_name, "id": stack["StackId"], "status": stack["StackStatus"],
                        "consoleUrl": f"https://{region}.console.aws.amazon.com/cloudformation/home?region={region}#/stacks/resources?stackId={quote(stack['StackId'], safe='')}"},
              "defaultPriceSource": outputs.get("PriceMode", "unknown"),
              "scheduleConfigured": parameters.get("EnableSchedule", "unknown"),
              "alertCooldownSeconds": parameters.get("AlertCooldownSeconds", "unknown"),
              "resources": [], "checks": [], "errors": [], "functions": [], "rules": []}
    for row in raw:
        kind, name = row["ResourceType"], row.get("PhysicalResourceId", "")
        if kind in ROLES:
            service, purpose = ROLES[kind]
            report["resources"].append({"logicalId": row["LogicalResourceId"], "name": name,
                                        "type": kind, "service": service, "purpose": purpose,
                                        "status": row["ResourceStatus"], "consoleUrl": console_url(region, kind, name)})

    def check(key, label, observe):
        try:
            status, detail = observe()
            report["checks"].append({"id": key, "label": label, "status": status, "detail": detail})
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
            report["checks"].append({"id": key, "label": label, "status": "unknown", "detail": f"Could not verify ({code})"})
            report["errors"].append({"operation": key, "code": code})

    def identity():
        report["accountId"] = client("sts").get_caller_identity()["Account"]
        return "pass", f"Signed in to account {report['accountId']}"
    check("identity", "AWS account", identity)
    report["checks"].append({"id": "stack", "label": "CloudFormation deployment", "status": "pass" if stack["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE") else "warning", "detail": stack["StackStatus"]})
    resources = report["resources"]
    api = next((row["name"] for row in resources if row["logicalId"] == "Api"), None)
    if api:
        def auth():
            routes = _pages(client("apigatewayv2"), "get_routes", "Items", ApiId=api)
            good = {"GET /products", "POST /checks"}.issubset({row["RouteKey"] for row in routes}) and all(row.get("AuthorizationType") == "AWS_IAM" for row in routes)
            report["routes"] = [{"route": row["RouteKey"], "authorization": row.get("AuthorizationType", "NONE"), "target": row.get("Target")} for row in routes]
            return ("pass" if good else "fail"), f"{len(routes)} routes inspected; " + ("all require AWS_IAM" if good else "not all routes require IAM")
        check("api-auth", "API IAM authorization", auth)
        def integration():
            rows = _pages(client("apigatewayv2"), "get_integrations", "Items", ApiId=api)
            target = next((row.get("target") for row in report.get("routes", []) if row["route"] == "POST /checks"), None)
            expected_bus = outputs.get("EventBusName")
            account = stack["StackId"].split(":")[4]
            allowed_buses = {expected_bus, f"arn:aws:events:{region}:{account}:event-bus/{expected_bus}"} if expected_bus else set()
            good = any(row.get("IntegrationSubtype") == "EventBridge-PutEvents"
                       and target == "integrations/" + row.get("IntegrationId", "")
                       and row.get("RequestParameters", {}).get("EventBusName") in allowed_buses for row in rows)
            return ("pass" if good else "fail"), "POST /checks → native EventBridge-PutEvents → expected event bus " + ("verified" if good else "not verified")
        check("api-integration", "Direct EventBridge integration", integration)
        def unsigned():
            status = probe(f"https://{api}.execute-api.{region}.amazonaws.com/v1/products")
            return ("pass" if status in (401, 403) else "fail"), f"Unsigned GET /products returned HTTP {status}"
        check("unsigned-api", "Unsigned request blocked", unsigned)
    else:
        report["checks"].append({"id": "api-auth", "label": "API Gateway", "status": "fail", "detail": "API resource not found in this stack"})
    for resource in resources:
        logical, name, kind = resource["logicalId"], resource["name"], resource["type"]
        if kind == "AWS::DynamoDB::Table":
            def table(name=name):
                data = client("dynamodb").describe_table(TableName=name)["Table"]
                mode = data.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")
                return ("pass" if data["TableStatus"] == "ACTIVE" else "warning"), f"{name}: {data['TableStatus']}, {mode}"
            check("table-" + logical, "DynamoDB " + logical, table)
            if logical == "PricesTable":
                def ttl(name=name):
                    data = client("dynamodb").describe_time_to_live(TableName=name)["TimeToLiveDescription"]
                    good = data.get("TimeToLiveStatus") == "ENABLED" and data.get("AttributeName") == "expiresAt"
                    return ("pass" if good else "warning"), f"expiresAt TTL: {data.get('TimeToLiveStatus', 'unknown')}"
                check("history-ttl", "Price-history retention", ttl)
        if kind == "AWS::Lambda::Function" and logical in ("PriceCheckFunction", "EvaluatePriceFunction"):
            def function(name=name):
                data = client("lambda").get_function_configuration(FunctionName=name)
                safe = {key: data.get(key) for key in ("FunctionName", "Runtime", "Handler", "MemorySize", "Timeout", "State", "LastModified", "LastUpdateStatus")}
                report["functions"].append(safe)
                healthy = data.get("State") == "Active" and data.get("LastUpdateStatus") == "Successful"
                return ("pass" if healthy else "warning"), f"{name}: {data.get('Runtime')}, {data.get('MemorySize')} MB, {data.get('Timeout')} s, {data.get('State')}"
            check("lambda-" + logical, "Lambda " + logical, function)
    bus = outputs.get("EventBusName")
    if bus:
        def rules():
            events = client("events")
            report["rules"] = _pages(events, "list_rules", "Rules", EventBusName=bus)
            # Default-bus schedules are separate from custom-bus event patterns.
            physical_rules = {row["name"] for row in resources if row["type"] == "AWS::Events::Rule"}
            defaults = _pages(events, "list_rules", "Rules", NamePrefix=stack_name)
            report["rules"].extend(row for row in defaults if row["Name"] in physical_rules)
            return ("pass" if report["rules"] else "warning"), "; ".join(f"{row['Name']}: {row['State']}" for row in report["rules"]) or "No rules returned"
        check("event-rules", "EventBridge rules and schedule", rules)
    if outputs.get("FailureQueueUrl"):
        def queue():
            attrs = client("sqs").get_queue_attributes(QueueUrl=outputs["FailureQueueUrl"], AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "SqsManagedSseEnabled"])["Attributes"]
            visible, processing = int(attrs.get("ApproximateNumberOfMessages", 0)), int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))
            encrypted = attrs.get("SqsManagedSseEnabled") == "true"
            return ("pass" if visible == 0 and processing == 0 and encrypted else "warning"), f"Approximate failure queue depth: {visible} waiting, {processing} in flight; SQS-managed encryption: {encrypted}"
        check("failure-queue", "Asynchronous failure queue", queue)
    def ses_identity():
        verified = client("sesv2").get_email_identity(EmailIdentity=email).get("VerifiedForSendingStatus", False)
        return ("pass" if verified else "fail"), "Demo sender identity " + ("verified" if verified else "not verified") + f" in {region}"
    check("ses-identity", "SES email identity", ses_identity)
    def ses_account():
        data = client("sesv2").get_account()
        return ("pass" if data.get("SendingEnabled") else "warning"), f"Sending enabled: {bool(data.get('SendingEnabled'))}; production access: {bool(data.get('ProductionAccessEnabled'))}. Sandbox requires verified recipients."
    check("ses-account", "SES sending mode", ses_account)
    report["sesConsoleUrl"] = f"https://{region}.console.aws.amazon.com/ses/home?region={region}#/verified-identities"
    report["note"] = "Point-in-time read-only inspection. Resource existence does not prove this request ran. Use a correlated cloud check and inbox evidence. No credentials, function environment variables or secret values are included."
    return report


def main():
    import boto3
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="pricing-personal")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--stack", default="dynamic-pricing-demo")
    parser.add_argument("--email", default="dhruv.agnihotri2024@vitstudent.ac.in")
    parser.add_argument("--report", type=Path, default=Path(".local/aws-inspection.json"))
    args = parser.parse_args()
    report = inspect_deployment(boto3.Session(profile_name=args.profile, region_name=args.region), args.stack, args.email)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"Saved read-only architecture evidence to {args.report}")


if __name__ == "__main__":
    main()
