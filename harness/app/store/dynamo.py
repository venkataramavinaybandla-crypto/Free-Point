"""DynamoDB storage implementations using boto3 for Free Point."""

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from .base import (
    ApiKeyStore,
    IdempotencyStore,
    InviteStore,
    Job,
    JobStatus,
    JobStore,
    Queue,
    SecretStore,
    SessionStore,
    UsageStore,
    UserStore,
)


def get_dynamo_client(region_name: Optional[str] = None) -> Any:
    region = region_name or os.environ.get("AWS_REGION", "ap-south-1")
    return boto3.client("dynamodb", region_name=region)


class DynamoDBJobStore(JobStore):
    def __init__(self, client: Optional[Any] = None, table_name: str = "free-point-jobs") -> None:
        self.client = client or get_dynamo_client()
        self.table_name = os.environ.get("DYNAMODB_JOBS_TABLE", table_name)

    async def create_job(
        self,
        task: str,
        declared_tools: List[str],
        idempotency_key: Optional[str] = None,
        model_url: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Job:
        job = Job(
            task=task,
            declared_tools=declared_tools,
            idempotency_key=idempotency_key,
            model_url=model_url,
            user_id=user_id,
        )
        item: Dict[str, Any] = {
            "id": {"S": job.id},
            "task": {"S": job.task},
            "declared_tools": {"SS": job.declared_tools if job.declared_tools else ["__empty__"]},
            "status": {"S": job.status.value},
            "created_at": {"S": job.created_at},
            "updated_at": {"S": job.updated_at},
            "steps": {"N": str(job.steps)},
        }
        if job.idempotency_key:
            item["idempotency_key"] = {"S": job.idempotency_key}
        if job.model_url:
            item["model_url"] = {"S": job.model_url}
        if job.user_id:
            item["user_id"] = {"S": job.user_id}
        if job.result is not None:
            item["result"] = {"S": json.dumps(job.result)}
        if job.error:
            item["error"] = {"S": job.error}
        if job.approval_artifact:
            item["approval_artifact"] = {"S": json.dumps(job.approval_artifact)}

        self.client.put_item(TableName=self.table_name, Item=item)
        return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        resp = self.client.get_item(TableName=self.table_name, Key={"id": {"S": job_id}})
        if "Item" not in resp:
            return None
        it = resp["Item"]
        tools_raw = it.get("declared_tools", {}).get("SS", [])
        tools = [t for t in tools_raw if t != "__empty__"]
        result = json.loads(it["result"]["S"]) if "result" in it else None
        artifact = json.loads(it["approval_artifact"]["S"]) if "approval_artifact" in it else None

        return Job(
            id=it["id"]["S"],
            task=it["task"]["S"],
            declared_tools=tools,
            status=JobStatus(it["status"]["S"]),
            created_at=it["created_at"]["S"],
            updated_at=it["updated_at"]["S"],
            steps=int(it.get("steps", {}).get("N", "0")),
            result=result,
            error=it.get("error", {}).get("S"),
            approval_artifact=artifact,
            idempotency_key=it.get("idempotency_key", {}).get("S"),
            model_url=it.get("model_url", {}).get("S"),
            user_id=it.get("user_id", {}).get("S"),
        )

    async def update_job(self, job: Job) -> Job:
        job.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        item: Dict[str, Any] = {
            "id": {"S": job.id},
            "task": {"S": job.task},
            "declared_tools": {"SS": job.declared_tools if job.declared_tools else ["__empty__"]},
            "status": {"S": job.status.value},
            "created_at": {"S": job.created_at},
            "updated_at": {"S": job.updated_at},
            "steps": {"N": str(job.steps)},
        }
        if job.idempotency_key:
            item["idempotency_key"] = {"S": job.idempotency_key}
        if job.model_url:
            item["model_url"] = {"S": job.model_url}
        if job.user_id:
            item["user_id"] = {"S": job.user_id}
        if job.result is not None:
            item["result"] = {"S": json.dumps(job.result)}
        if job.error:
            item["error"] = {"S": job.error}
        if job.approval_artifact:
            item["approval_artifact"] = {"S": json.dumps(job.approval_artifact)}

        self.client.put_item(TableName=self.table_name, Item=item)
        return job

    async def get_by_idempotency_key(self, key: str) -> Optional[Job]:
        # Scan or query by idempotency key
        resp = self.client.scan(
            TableName=self.table_name,
            FilterExpression="idempotency_key = :k",
            ExpressionAttributeValues={":k": {"S": key}},
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return None
        return await self.get_job(items[0]["id"]["S"])


class DynamoDBQueue(Queue):
    def __init__(self) -> None:
        import asyncio

        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def dequeue(self, timeout: Optional[float] = None) -> Optional[str]:
        import asyncio

        try:
            if timeout:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return await self._queue.get()
        except (asyncio.TimeoutError, TimeoutError):
            return None


class DynamoDBInviteStore(InviteStore):
    def __init__(self, client: Optional[Any] = None, table_name: str = "free-point-invites") -> None:
        self.client = client or get_dynamo_client()
        self.table_name = os.environ.get("DYNAMODB_INVITES_TABLE", table_name)

    async def create_invite(self, code_hash: str, expires_at: int) -> None:
        self.client.put_item(
            TableName=self.table_name,
            Item={
                "code_hash": {"S": code_hash},
                "expires_at": {"N": str(expires_at)},
                "used": {"BOOL": False},
                "created_at": {"N": str(int(time.time()))},
            },
        )

    async def consume_invite(self, code_hash: str) -> bool:
        now = int(time.time())
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key={"code_hash": {"S": code_hash}},
                ConditionExpression="attribute_exists(code_hash) AND used = :false AND expires_at > :now",
                UpdateExpression="SET used = :true, consumed_at = :now",
                ExpressionAttributeValues={
                    ":false": {"BOOL": False},
                    ":true": {"BOOL": True},
                    ":now": {"N": str(now)},
                },
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise


class DynamoDBUserStore(UserStore):
    def __init__(self, client: Optional[Any] = None, table_name: str = "free-point-users") -> None:
        self.client = client or get_dynamo_client()
        self.table_name = os.environ.get("DYNAMODB_USERS_TABLE", table_name)

    async def create_user(self, user_id: str, email: str, password_hash: str, created_at: int) -> Dict[str, Any]:
        normalized_email = email.strip().lower()
        # Verify email not already registered
        existing = await self.get_user_by_email(normalized_email)
        if existing:
            raise ValueError("Email already registered")

        item = {
            "id": {"S": user_id},
            "email": {"S": normalized_email},
            "password_hash": {"S": password_hash},
            "created_at": {"N": str(created_at)},
        }
        self.client.put_item(
            TableName=self.table_name,
            Item=item,
            ConditionExpression="attribute_not_exists(id)",
        )
        return {
            "id": user_id,
            "email": normalized_email,
            "password_hash": password_hash,
            "created_at": created_at,
        }

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalized = email.strip().lower()
        resp = self.client.query(
            TableName=self.table_name,
            IndexName="email-index",
            KeyConditionExpression="email = :e",
            ExpressionAttributeValues={":e": {"S": normalized}},
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return None
        it = items[0]
        return {
            "id": it["id"]["S"],
            "email": it["email"]["S"],
            "password_hash": it["password_hash"]["S"],
            "created_at": int(it["created_at"]["N"]),
        }

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        resp = self.client.get_item(TableName=self.table_name, Key={"id": {"S": user_id}})
        if "Item" not in resp:
            return None
        it = resp["Item"]
        return {
            "id": it["id"]["S"],
            "email": it["email"]["S"],
            "password_hash": it["password_hash"]["S"],
            "created_at": int(it["created_at"]["N"]),
        }


class DynamoDBSessionStore(SessionStore):
    def __init__(self, client: Optional[Any] = None, table_name: str = "free-point-sessions") -> None:
        self.client = client or get_dynamo_client()
        self.table_name = os.environ.get("DYNAMODB_SESSIONS_TABLE", table_name)

    async def create_session(
        self, session_hash: str, user_id: str, csrf_token: str, expires_at: int
    ) -> Dict[str, Any]:
        item = {
            "session_hash": {"S": session_hash},
            "user_id": {"S": user_id},
            "csrf_token": {"S": csrf_token},
            "expires_at": {"N": str(expires_at)},
            "created_at": {"N": str(int(time.time()))},
        }
        self.client.put_item(TableName=self.table_name, Item=item)
        return {
            "session_hash": session_hash,
            "user_id": user_id,
            "csrf_token": csrf_token,
            "expires_at": expires_at,
        }

    async def get_session(self, session_hash: str) -> Optional[Dict[str, Any]]:
        resp = self.client.get_item(TableName=self.table_name, Key={"session_hash": {"S": session_hash}})
        if "Item" not in resp:
            return None
        it = resp["Item"]
        expires_at = int(it["expires_at"]["N"])
        if expires_at <= int(time.time()):
            await self.delete_session(session_hash)
            return None
        return {
            "session_hash": it["session_hash"]["S"],
            "user_id": it["user_id"]["S"],
            "csrf_token": it["csrf_token"]["S"],
            "expires_at": expires_at,
        }

    async def delete_session(self, session_hash: str) -> None:
        self.client.delete_item(TableName=self.table_name, Key={"session_hash": {"S": session_hash}})


class DynamoDBApiKeyStore(ApiKeyStore):
    def __init__(self, client: Optional[Any] = None, table_name: str = "free-point-api_keys") -> None:
        self.client = client or get_dynamo_client()
        self.table_name = os.environ.get("DYNAMODB_API_KEYS_TABLE", table_name)

    async def create_key(
        self, key_id: str, user_id: str, prefix: str, key_hash: str, created_at: int
    ) -> Dict[str, Any]:
        item = {
            "prefix": {"S": prefix},
            "key_id": {"S": key_id},
            "user_id": {"S": user_id},
            "key_hash": {"S": key_hash},
            "created_at": {"N": str(created_at)},
            "revoked": {"BOOL": False},
        }
        self.client.put_item(TableName=self.table_name, Item=item)
        return {
            "key_id": key_id,
            "user_id": user_id,
            "prefix": prefix,
            "key_hash": key_hash,
            "created_at": created_at,
            "revoked": False,
            "last_used": None,
        }

    async def get_key_by_prefix(self, prefix: str) -> Optional[Dict[str, Any]]:
        resp = self.client.get_item(TableName=self.table_name, Key={"prefix": {"S": prefix}})
        if "Item" not in resp:
            return None
        it = resp["Item"]
        last_used = int(it["last_used"]["N"]) if "last_used" in it else None
        return {
            "key_id": it["key_id"]["S"],
            "user_id": it["user_id"]["S"],
            "prefix": it["prefix"]["S"],
            "key_hash": it["key_hash"]["S"],
            "created_at": int(it["created_at"]["N"]),
            "revoked": it.get("revoked", {}).get("BOOL", False),
            "last_used": last_used,
        }

    async def get_key_by_id(self, key_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        keys = await self.list_keys_for_user(user_id)
        for k in keys:
            if k["key_id"] == key_id:
                return k
        return None

    async def list_keys_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        resp = self.client.query(
            TableName=self.table_name,
            IndexName="user_id-index",
            KeyConditionExpression="user_id = :u",
            ExpressionAttributeValues={":u": {"S": user_id}},
        )
        results = []
        for it in resp.get("Items", []):
            last_used = int(it["last_used"]["N"]) if "last_used" in it else None
            results.append(
                {
                    "key_id": it["key_id"]["S"],
                    "user_id": it["user_id"]["S"],
                    "prefix": it["prefix"]["S"],
                    "key_hash": it["key_hash"]["S"],
                    "created_at": int(it["created_at"]["N"]),
                    "revoked": it.get("revoked", {}).get("BOOL", False),
                    "last_used": last_used,
                }
            )
        return results

    async def count_active_keys(self, user_id: str) -> int:
        keys = await self.list_keys_for_user(user_id)
        return sum(1 for k in keys if not k["revoked"])

    async def rotate_key(
        self, old_key_id: str, user_id: str, new_key_id: str, new_prefix: str, new_hash: str, created_at: int
    ) -> Optional[Dict[str, Any]]:
        old_doc = await self.get_key_by_id(old_key_id, user_id)
        if not old_doc:
            return None

        # Revoke old key
        await self.revoke_key(old_key_id, user_id)
        # Create new key
        return await self.create_key(
            key_id=new_key_id,
            user_id=user_id,
            prefix=new_prefix,
            key_hash=new_hash,
            created_at=created_at,
        )

    async def revoke_key(self, key_id: str, user_id: str) -> bool:
        doc = await self.get_key_by_id(key_id, user_id)
        if not doc:
            return False
        self.client.update_item(
            TableName=self.table_name,
            Key={"prefix": {"S": doc["prefix"]}},
            UpdateExpression="SET revoked = :true",
            ExpressionAttributeValues={":true": {"BOOL": True}},
        )
        return True

    async def update_last_used(self, key_id: str, last_used: int) -> None:
        # Scan or find key prefix
        resp = self.client.scan(
            TableName=self.table_name,
            FilterExpression="key_id = :kid",
            ExpressionAttributeValues={":kid": {"S": key_id}},
            Limit=1,
        )
        items = resp.get("Items", [])
        if items:
            prefix = items[0]["prefix"]["S"]
            self.client.update_item(
                TableName=self.table_name,
                Key={"prefix": {"S": prefix}},
                UpdateExpression="SET last_used = :lu",
                ExpressionAttributeValues={":lu": {"N": str(last_used)}},
            )


class DynamoDBUsageStore(UsageStore):
    def __init__(self, client: Optional[Any] = None, table_name: str = "free-point-usage_counters") -> None:
        self.client = client or get_dynamo_client()
        self.table_name = os.environ.get("DYNAMODB_USAGE_TABLE", table_name)

    async def record_and_check(
        self, key_id: str, rate_limit: int, daily_quota: int
    ) -> Tuple[bool, bool, Dict[str, Any]]:
        now = int(time.time())
        window_epoch = (now // 60) * 60
        rate_reset = window_epoch + 60
        retry_after_rate = max(1, rate_reset - now)

        day_epoch = (now // 86400) * 86400
        quota_reset = day_epoch + 86400
        retry_after_quota = max(1, quota_reset - now)

        rate_key = f"rate:{key_id}:{window_epoch}"
        quota_key = f"quota:{key_id}:{day_epoch}"

        # Atomic counter update with TTL
        resp_rate = self.client.update_item(
            TableName=self.table_name,
            Key={"id": {"S": rate_key}},
            UpdateExpression="ADD #cnt :val SET expires_at = if_not_exists(expires_at, :exp)",
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={":val": {"N": "1"}, ":exp": {"N": str(window_epoch + 120)}},
            ReturnValues="UPDATED_NEW",
        )
        rate_count = int(resp_rate["Attributes"]["count"]["N"])

        resp_quota = self.client.update_item(
            TableName=self.table_name,
            Key={"id": {"S": quota_key}},
            UpdateExpression="ADD #cnt :val SET expires_at = if_not_exists(expires_at, :exp)",
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={":val": {"N": "1"}, ":exp": {"N": str(day_epoch + 172800)}},
            ReturnValues="UPDATED_NEW",
        )
        quota_count = int(resp_quota["Attributes"]["count"]["N"])

        rate_exceeded = rate_count > rate_limit
        quota_exceeded = quota_count > daily_quota

        info = {
            "rate_limit": rate_limit,
            "rate_remaining": max(0, rate_limit - rate_count),
            "rate_reset": rate_reset,
            "daily_quota": daily_quota,
            "quota_remaining": max(0, daily_quota - quota_count),
            "retry_after": retry_after_quota if quota_exceeded else retry_after_rate,
        }
        return rate_exceeded, quota_exceeded, info

    async def get_usage(self, key_id: str, rate_limit: int, daily_quota: int) -> Dict[str, Any]:
        now = int(time.time())
        window_epoch = (now // 60) * 60
        day_epoch = (now // 86400) * 86400

        rate_resp = self.client.get_item(
            TableName=self.table_name, Key={"id": {"S": f"rate:{key_id}:{window_epoch}"}}
        )
        quota_resp = self.client.get_item(
            TableName=self.table_name, Key={"id": {"S": f"quota:{key_id}:{day_epoch}"}}
        )
        rate_count = int(rate_resp["Item"]["count"]["N"]) if "Item" in rate_resp else 0
        quota_count = int(quota_resp["Item"]["count"]["N"]) if "Item" in quota_resp else 0

        return {
            "key_id": key_id,
            "rate_limit": rate_limit,
            "rate_remaining": max(0, rate_limit - rate_count),
            "rate_reset": window_epoch + 60,
            "daily_quota": daily_quota,
            "quota_remaining": max(0, daily_quota - quota_count),
        }


class DynamoDBIdempotencyStore(IdempotencyStore):
    def __init__(self, client: Optional[Any] = None, table_name: str = "free-point-idempotency") -> None:
        self.client = client or get_dynamo_client()
        self.table_name = os.environ.get("DYNAMODB_IDEMPOTENCY_TABLE", table_name)

    async def get_record(self, composite_key: str) -> Optional[Dict[str, Any]]:
        resp = self.client.get_item(TableName=self.table_name, Key={"key": {"S": composite_key}})
        if "Item" not in resp:
            return None
        it = resp["Item"]
        expires_at = int(it["expires_at"]["N"])
        if expires_at <= int(time.time()):
            self.client.delete_item(TableName=self.table_name, Key={"key": {"S": composite_key}})
            return None
        return {
            "composite_key": it["key"]["S"],
            "user_id": it["user_id"]["S"],
            "request_hash": it["request_hash"]["S"],
            "status_code": int(it["status_code"]["N"]),
            "response_body": json.loads(it["response_body"]["S"]),
            "expires_at": expires_at,
        }

    async def save_record(
        self,
        composite_key: str,
        user_id: str,
        request_hash: str,
        status_code: int,
        response_body: Any,
        expires_at: int,
    ) -> None:
        item = {
            "key": {"S": composite_key},
            "user_id": {"S": user_id},
            "request_hash": {"S": request_hash},
            "status_code": {"N": str(status_code)},
            "response_body": {"S": json.dumps(response_body)},
            "expires_at": {"N": str(expires_at)},
            "created_at": {"N": str(int(time.time()))},
        }
        self.client.put_item(TableName=self.table_name, Item=item)


class DynamoDBSecretStore(SecretStore):
    def __init__(self, client: Optional[Any] = None, table_name: str = "free-point-user_secrets") -> None:
        self.client = client or get_dynamo_client()
        self.table_name = os.environ.get("DYNAMODB_SECRETS_TABLE", table_name)

    async def set_secret(self, user_id: str, name: str, encrypted_value: str, updated_at: int) -> None:
        item = {
            "user_id": {"S": user_id},
            "name": {"S": name},
            "encrypted_value": {"S": encrypted_value},
            "updated_at": {"N": str(updated_at)},
        }
        self.client.put_item(TableName=self.table_name, Item=item)

    async def get_secret(self, user_id: str, name: str) -> Optional[str]:
        resp = self.client.get_item(
            TableName=self.table_name, Key={"user_id": {"S": user_id}, "name": {"S": name}}
        )
        if "Item" not in resp:
            return None
        return resp["Item"]["encrypted_value"]["S"]

    async def list_secrets(self, user_id: str) -> List[Dict[str, Any]]:
        resp = self.client.query(
            TableName=self.table_name,
            KeyConditionExpression="user_id = :u",
            ExpressionAttributeValues={":u": {"S": user_id}},
        )
        results = []
        for it in resp.get("Items", []):
            results.append({"name": it["name"]["S"], "updated_at": int(it["updated_at"]["N"])})
        return sorted(results, key=lambda x: x["name"])
