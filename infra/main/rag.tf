resource "aws_s3_bucket" "rag_indexes" {
  bucket        = "free-point-rag-indexes"
  force_destroy = true

  tags = {
    Name = "free-point-rag-indexes"
  }
}

resource "aws_s3_bucket_versioning" "rag_indexes" {
  bucket = aws_s3_bucket.rag_indexes.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "rag_indexes" {
  bucket                  = aws_s3_bucket.rag_indexes.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "rag_indexes" {
  bucket = aws_s3_bucket.rag_indexes.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_dynamodb_table" "rag_pointers" {
  name         = "free-point-rag_pointers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  tags = {
    Name = "free-point-rag_pointers"
  }
}

resource "aws_iam_policy" "rag_mcp" {
  name        = "free-point-rag-mcp-policy"
  description = "Least-privilege S3 and DynamoDB access for the RAG MCP server"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RagIndexObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = [
          "${aws_s3_bucket.rag_indexes.arn}/index/*"
        ]
      },
      {
        Sid    = "RagPointerTable"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.rag_pointers.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rag_mcp" {
  role       = aws_iam_role.app_instance_role.name
  policy_arn = aws_iam_policy.rag_mcp.arn
}
