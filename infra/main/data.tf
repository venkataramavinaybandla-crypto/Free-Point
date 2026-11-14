resource "aws_dynamodb_table" "jobs" {
  name         = "free-point-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = {
    Name = "free-point-jobs"
  }
}

resource "aws_dynamodb_table" "idempotency" {
  name         = "free-point-idempotency"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key"

  attribute {
    name = "key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = {
    Name = "free-point-idempotency"
  }
}

resource "aws_dynamodb_table" "users" {
  name         = "free-point-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "email"
    type = "S"
  }

  global_secondary_index {
    name            = "email-index"
    hash_key        = "email"
    projection_type = "ALL"
  }

  tags = {
    Name = "free-point-users"
  }
}

resource "aws_dynamodb_table" "invites" {
  name         = "free-point-invites"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "code_hash"

  attribute {
    name = "code_hash"
    type = "S"
  }

  tags = {
    Name = "free-point-invites"
  }
}

resource "aws_dynamodb_table" "sessions" {
  name         = "free-point-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_hash"

  attribute {
    name = "session_hash"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = {
    Name = "free-point-sessions"
  }
}

resource "aws_dynamodb_table" "api_keys" {
  name         = "free-point-api_keys"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "prefix"

  attribute {
    name = "prefix"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  global_secondary_index {
    name            = "user_id-index"
    hash_key        = "user_id"
    projection_type = "ALL"
  }

  tags = {
    Name = "free-point-api_keys"
  }
}

resource "aws_dynamodb_table" "usage_counters" {
  name         = "free-point-usage_counters"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = {
    Name = "free-point-usage_counters"
  }
}

resource "aws_dynamodb_table" "user_secrets" {
  name         = "free-point-user_secrets"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "name"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "name"
    type = "S"
  }

  tags = {
    Name = "free-point-user_secrets"
  }
}
