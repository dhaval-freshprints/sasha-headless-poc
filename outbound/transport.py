"""Submit messages to FreshFlow once; downstream delivery belongs to FreshFlow."""

import json


class PublishError(Exception):
    pass


class SqsTransport:
    def __init__(self, settings, client=None):
        self.settings = settings
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("sqs", region_name=settings.region, config=Config(
                retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=20))
        self.client = client

    def publish(self, row):
        from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError

        parameters = {
            "QueueUrl": self.settings.queue_url,
            "MessageBody": json.dumps(row["payload"], ensure_ascii=False),
        }
        if row["attributes"]:
            parameters["MessageAttributes"] = {
                key: {"StringValue": str(value), "DataType": "String"}
                for key, value in row["attributes"].items()
            }
        try:
            result = self.client.send_message(**parameters)
        except (NoCredentialsError, PartialCredentialsError) as error:
            raise PublishError("AWS credentials are not configured") from error
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "SqsError")
            raise PublishError(code) from error
        except Exception as error:
            raise PublishError(type(error).__name__) from error
        if not result.get("MessageId"):
            raise PublishError("SQS returned no message ID")
        return result["MessageId"]
