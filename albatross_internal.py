import json
import logging
from typing import Optional
import boto3
import os
import requests

logger = logging.getLogger()
logger.setLevel(logging.WARNING)

DEVSERVER = "devserver"
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK")


def handle(event, context):
    client = boto3.client("ec2")
    logger.warn(f"Received event {event}")

    server = _find_devserver(client)
    if not server:
        return {"statusCode": 400, "body": "Server not found"}
    if event["detail-type"] == "EC2 Instance State-change Notification":
        return _process_instance_state_change(client, event, server)
    if event["detail-type"] == "Scheduled Event":
        return _process_cron(client, event, server)

    return {"statusCode": 200, "body": "noop"}


def _process_cron(client, event, server):
    images = client.describe_images(
        Filters=[{"Name": "tag:application", "Values": [DEVSERVER]}]
    )
    if not images["Images"]:
        return {"statusCode": 200, "body": "noop"}

    image = images["Images"][0]
    if image["State"] == "available":
        slack_str = "Successfully terminated image"
        try:
            client.terminate_instances(InstanceIds=[server["InstanceId"]])
        except Exception as e:
            slack_str = f"Couldn't terminate instance: {e}"
        requests.post(SLACK_WEBHOOK, json={"text": slack_str})
        return {"statusCode": 200, "body": "Terminated instance"}

    return {"statusCode": 200, "body": "noop"}


def _process_instance_state_change(client, event, server):
    if server["InstanceId"] != event["detail"]["instance-id"]:
        return {"statusCode": 400, "body": "Irrelevant instance id"}

    if event["detail"]["state"] != "stopped":
        return {"statusCode": 400, "body": "Irrelevant state"}

    instance_id = server["InstanceId"]
    slack_str = "Instance has stopped, image created"
    try:
        res = client.create_image(InstanceId=instance_id, Name="albatross")
        client.create_tags(
            Resources=[res["ImageId"]],
            Tags=[{"Key": "application", "Value": DEVSERVER}],
        )
    except Exception as e:
        slack_str = f"Couldn't create image on shut down: `{e}`"

    requests.post(SLACK_WEBHOOK, json={"text": slack_str})
    return {"statusCode": 200, "body": json.dumps("Creating snapshot")}


def _find_devserver(client) -> Optional[any]:
    reservations = _get_ec2_instances(client)["Reservations"]
    if (
        not reservations
        or not reservations[0]["Instances"]
        or not reservations[0]["Instances"][0]
    ):
        return None

    return reservations[0]["Instances"][0]


def _get_ec2_instances(client):
    return client.describe_instances(
        Filters=[{"Name": "tag:application", "Values": [DEVSERVER]}]
    )