"""Tests for DynamoDB and KMS paths using moto."""

import os
import time

import boto3
import httpx
import pytest
from moto import mock_aws

from harness.app.auth.tokens import generate_invite_code, hash_sha256
from harness.app.crypto.envelope import EnvelopeEncryptor, KMSKeyProvider
from harness.app.main import app
from harness.app.store.dynamo import (
    DynamoDBApiKeyStore,
    DynamoDBInviteStore,
    DynamoDBSecretStore,
    DynamoDBUsageStore,
    DynamoDBUserStore,
)
from harness.app.store.factory import reset_stores

REGION = "ap-south-1"


def setup_dynamo_tables(dynamo_client):
    """Create all Free Point DynamoDB tables in mock environment."""
    dynamo_client.create_table(
        TableName="free-point-jobs",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamo_client.create_table(
        TableName="free-point-idempotency",
        KeySchema=[{"AttributeName": "key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamo_client.create_table(
        TableName="free-point-users",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "id", "AttributeType": "S"},
            {"AttributeName": "email", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "email-index",
                "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamo_client.create_table(
        TableName="free-point-invites",
        KeySchema=[{"AttributeName": "code_hash", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "code_hash", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamo_client.create_table(
        TableName="free-point-sessions",
        KeySchema=[{"AttributeName": "session_hash", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "session_hash", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamo_client.create_table(
        TableName="free-point-api_keys",
        KeySchema=[{"AttributeName": "prefix", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "prefix", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "user_id-index",
                "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamo_client.create_table(
        TableName="free-point-usage_counters",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamo_client.create_table(
        TableName="free-point-user_secrets",
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "name", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "name", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@mock_aws
def test_kms_envelope_encryption_with_moto():
    """Verify KMS GenerateDataKey and Decrypt with moto mock KMS."""
    kms = boto3.client("kms", region_name=REGION)
    key_resp = kms.create_key(Description="Test CMK")
    key_id = key_resp["KeyMetadata"]["KeyId"]

    provider = KMSKeyProvider(key_id=key_id, kms_client=kms, region_name=REGION)
    encryptor = EnvelopeEncryptor(key_provider=provider)

    secret = "my-top-secret-model-token"
    enc = encryptor.encrypt(plaintext=secret, user_id="usr-123", tool="model_url")
    dec = encryptor.decrypt(enc, user_id="usr-123", tool="model_url")
    assert dec == secret

    # Encryption context mismatch must fail
    with pytest.raises(ValueError):
        encryptor.decrypt(enc, user_id="usr-999", tool="model_url")


@pytest.mark.asyncio
async def test_dynamodb_stores_direct():
    """Verify DynamoDB store implementations directly with moto."""
    with mock_aws():
        dynamo = boto3.client("dynamodb", region_name=REGION)
        setup_dynamo_tables(dynamo)

        # 1. Invite store
        invite_store = DynamoDBInviteStore(client=dynamo)
        await invite_store.create_invite("hash123", expires_at=int(time.time()) + 1000)
        consumed = await invite_store.consume_invite("hash123")
        assert consumed is True
        # Second consume fails
        consumed_again = await invite_store.consume_invite("hash123")
        assert consumed_again is False

        # 2. User store
        user_store = DynamoDBUserStore(client=dynamo)
        user = await user_store.create_user("usr-1", "test@domain.com", "hashpwd", int(time.time()))
        assert user["email"] == "test@domain.com"
        fetched = await user_store.get_user_by_email("test@domain.com")
        assert fetched is not None
        assert fetched["id"] == "usr-1"

        # 3. ApiKey store
        key_store = DynamoDBApiKeyStore(client=dynamo)
        await key_store.create_key("k-1", "usr-1", "pre12345", "hashval", int(time.time()))
        k = await key_store.get_key_by_prefix("pre12345")
        assert k is not None
        assert k["key_id"] == "k-1"

        # 4. Usage store (atomic ADD counters)
        usage_store = DynamoDBUsageStore(client=dynamo)
        rate_exc, quota_exc, info = await usage_store.record_and_check("k-1", rate_limit=2, daily_quota=10)
        assert rate_exc is False
        assert info["rate_remaining"] == 1
        rate_exc2, _, info2 = await usage_store.record_and_check("k-1", rate_limit=2, daily_quota=10)
        assert rate_exc2 is False
        assert info2["rate_remaining"] == 0
        rate_exc3, _, _ = await usage_store.record_and_check("k-1", rate_limit=2, daily_quota=10)
        assert rate_exc3 is True

        # 5. Secret store
        secret_store = DynamoDBSecretStore(client=dynamo)
        await secret_store.set_secret("usr-1", "token", "enc_data", int(time.time()))
        val = await secret_store.get_secret("usr-1", "token")
        assert val == "enc_data"
        listed = await secret_store.list_secrets("usr-1")
        assert len(listed) == 1
        assert listed[0]["name"] == "token"


@pytest.mark.asyncio
async def test_end_to_end_api_with_dynamodb_backend():
    """Verify full FastAPI routes work end-to-end with STORE_BACKEND=dynamodb."""
    with mock_aws():
        os.environ["STORE_BACKEND"] = "dynamodb"
        os.environ["AWS_REGION"] = REGION
        os.environ["AWS_DEFAULT_REGION"] = REGION

        dynamo = boto3.client("dynamodb", region_name=REGION)
        setup_dynamo_tables(dynamo)
        reset_stores()

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
                # 1. Create invite in DynamoDB
                invite_store = DynamoDBInviteStore(client=dynamo)
                code = generate_invite_code()
                await invite_store.create_invite(hash_sha256(code), int(time.time()) + 3600)

                # 2. Signup through API
                signup_resp = await client.post(
                    "/auth/signup",
                    json={"invite": code, "email": "dynamo_user@example.com", "password": "Password123!"},
                )
                assert signup_resp.status_code == 201
                csrf = signup_resp.json()["csrf_token"]

                # 3. Create API key
                key_resp = await client.post("/keys", headers={"X-CSRF-Token": csrf})
                assert key_resp.status_code == 201
                api_key = key_resp.json()["key"]

                # 4. Use API key on /v1/jobs
                job_resp = await client.post(
                    "/v1/jobs",
                    json={"task": "Echo on DynamoDB", "tools": ["echo"]},
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                assert job_resp.status_code == 201
                assert job_resp.json()["id"].startswith("job-")

                # 5. Check usage counters
                usage_resp = await client.get("/v1/usage", headers={"Authorization": f"Bearer {api_key}"})
                assert usage_resp.status_code == 200
                assert "X-RateLimit-Remaining" in usage_resp.headers
        finally:
            os.environ["STORE_BACKEND"] = "memory"
            reset_stores()
