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
