"""In-memory S3/DynamoDB doubles for the compose smoke run.

Only used when ``RAG_MOCK_BACKEND=true``. The doubles preserve the conditional
write semantics the RAG service depends on: version objects use
``If-None-Match: *`` and pointers use a compare-and-swap update.
"""

from __future__ import annotations

import io
import threading
from typing import Any

from botocore.exceptions import ClientError


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class InMemoryS3:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}
        self._lock = threading.Lock()

    def put_object(self, Bucket: str, Key: str, Body: Any, **kwargs: Any) -> dict[str, Any]:
        data = Body.encode("utf-8") if isinstance(Body, str) else bytes(Body)
        with self._lock:
            if kwargs.get("IfNoneMatch") == "*" and (Bucket, Key) in self._objects:
                raise _client_error("PreconditionFailed", "PutObject")
            self._objects[(Bucket, Key)] = data
        return {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        with self._lock:
            data = self._objects.get((Bucket, Key))
        if data is None:
            raise _client_error("NoSuchKey", "GetObject")
        return {"Body": io.BytesIO(data)}


class InMemoryDynamo:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()

    def get_item(self, TableName: str, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(Key["user_id"]["S"])
        if item is None:
            return {}
        return {"Item": {"user_id": {"S": Key["user_id"]["S"]}, "version": {"N": str(item["version"])}}}

    def update_item(
        self,
        TableName: str,
        Key: dict[str, Any],
        ConditionExpression: str,
        ExpressionAttributeValues: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        user_id = Key["user_id"]["S"]
        new_version = int(ExpressionAttributeValues[":new"]["N"])
        with self._lock:
            current = self._items.get(user_id)
            if "attribute_not_exists" in ConditionExpression:
                if current is not None:
                    raise _client_error("ConditionalCheckFailedException", "UpdateItem")
            else:
                expected = int(ExpressionAttributeValues[":expected"]["N"])
                if current is None or current["version"] != expected:
                    raise _client_error("ConditionalCheckFailedException", "UpdateItem")
            self._items[user_id] = {"version": new_version}
        return {}


def mock_clients() -> tuple[InMemoryS3, InMemoryDynamo]:
    return InMemoryS3(), InMemoryDynamo()
