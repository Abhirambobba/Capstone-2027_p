from __future__ import annotations

import base64
import json
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


class EmployeeAlreadyExistsError(Exception):
    pass


class EmployeeTableUnavailableError(Exception):
    pass


class EmployeeRepositoryValidationError(Exception):
    pass


class EmployeeIdentityConflictError(Exception):
    pass


class EmployeeRepository:
    def __init__(self, table: Any):
        self.table = table

    def create(self, item: dict[str, Any]) -> dict[str, Any]:
        github_user_id = (item.get("github") or {}).get("user_id")
        if github_user_id:
            item["github_user_id"] = str(github_user_id)
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(employee_id)",
            )
        except ClientError as error:
            self._raise_for_client_error(error)
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise EmployeeAlreadyExistsError from error
            raise
        return item

    def get(self, employee_id: str) -> dict[str, Any] | None:
        try:
            response = self.table.get_item(Key={"employee_id": employee_id})
        except ClientError as error:
            self._raise_for_client_error(error)
            raise
        return response.get("Item")

    def find_by_cognito_user_id(self, cognito_user_id: str) -> dict[str, Any] | None:
        exclusive_start_key = None
        matched_item = None
        while True:
            parameters: dict[str, Any] = {
                "IndexName": "CognitoUserIndex",
                "KeyConditionExpression": "cognito_user_id = :cognito_user_id",
                "ExpressionAttributeValues": {":cognito_user_id": cognito_user_id},
            }
            if exclusive_start_key:
                parameters["ExclusiveStartKey"] = exclusive_start_key
            try:
                response = self.table.query(**parameters)
            except ClientError as error:
                self._raise_for_client_error(error)
                raise
            items = response.get("Items", [])
            if items:
                if matched_item is not None or len(items) > 1:
                    raise EmployeeIdentityConflictError
                matched_item = items[0]
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                return matched_item

    def find_by_github_user_id(self, github_user_id: str) -> dict[str, Any] | None:
        try:
            response = self.table.query(
                IndexName="GitHubUserIndex",
                KeyConditionExpression=Key("github_user_id").eq(github_user_id),
                Limit=2,
            )
        except ClientError as error:
            self._raise_for_client_error(error)
            raise
        items = response.get("Items", [])
        if len(items) > 1:
            raise EmployeeIdentityConflictError
        return items[0] if items else None

    def list(self, limit: int, exclusive_start_key: dict[str, Any] | None):
        parameters: dict[str, Any] = {"Limit": limit}
        if exclusive_start_key:
            parameters["ExclusiveStartKey"] = exclusive_start_key
        try:
            response = self.table.scan(**parameters)
        except ClientError as error:
            self._raise_for_client_error(error)
            raise
        return response.get("Items", []), response.get("LastEvaluatedKey")

    def update(
        self,
        employee_id: str,
        fields: dict[str, Any],
        remove_fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        remove_fields = remove_fields or []
        if not fields and not remove_fields:
            return self.get(employee_id)

        expression_names = {f"#{field}": field for field in [*fields, *remove_fields]}
        expression_values = {
            f":{field}": value for field, value in fields.items()
        }
        clauses = []
        if fields:
            clauses.append("SET " + ", ".join(f"#{field} = :{field}" for field in fields))
        if remove_fields:
            clauses.append("REMOVE " + ", ".join(f"#{field}" for field in remove_fields))
        try:
            response = self.table.update_item(
                Key={"employee_id": employee_id},
                UpdateExpression=" ".join(clauses),
                ExpressionAttributeNames=expression_names,
                **({"ExpressionAttributeValues": expression_values} if expression_values else {}),
                ConditionExpression="attribute_exists(employee_id)",
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            self._raise_for_client_error(error)
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return response.get("Attributes")

    def find_by_device_id(self, device_id: str) -> dict[str, Any] | None:
        exclusive_start_key = None
        while True:
            parameters: dict[str, Any] = {
                "Limit": 100,
                "FilterExpression": Attr("agent.device_id").eq(device_id),
                "ProjectionExpression": "#employee_id, #agent",
                "ExpressionAttributeNames": {
                    "#employee_id": "employee_id",
                    "#agent": "agent",
                },
            }
            if exclusive_start_key:
                parameters["ExclusiveStartKey"] = exclusive_start_key
            try:
                response = self.table.scan(**parameters)
            except ClientError as error:
                self._raise_for_client_error(error)
                raise
            items = response.get("Items", [])
            if items:
                return self.get(items[0]["employee_id"])
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                return None

    @staticmethod
    def encode_key(key: dict[str, Any] | None) -> str | None:
        if not key:
            return None
        payload = json.dumps(key, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @staticmethod
    def decode_key(token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        try:
            padding = "=" * (-len(token) % 4)
            value = base64.urlsafe_b64decode(f"{token}{padding}")
            key = json.loads(value.decode())
            if not isinstance(key, dict) or set(key) != {"employee_id"}:
                raise ValueError
            return key
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EmployeeRepositoryValidationError("Invalid next_token") from error

    @staticmethod
    def _raise_for_client_error(error: ClientError) -> None:
        code = error.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            raise EmployeeTableUnavailableError from error
        if code == "ValidationException":
            raise EmployeeRepositoryValidationError(
                error.response["Error"].get("Message", "Invalid DynamoDB request")
            ) from error
