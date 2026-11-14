output "state_bucket_id" {
  description = "Name of the S3 bucket storing Terraform remote state"
  value       = aws_s3_bucket.state.id
}

output "state_bucket_arn" {
  description = "ARN of the S3 bucket storing Terraform remote state"
  value       = aws_s3_bucket.state.arn
}

output "dynamodb_lock_table_name" {
  description = "Name of the DynamoDB table used for Terraform state locking"
  value       = aws_dynamodb_table.locks.name
}

output "dynamodb_lock_table_arn" {
  description = "ARN of the DynamoDB table used for Terraform state locking"
  value       = aws_dynamodb_table.locks.arn
}

output "budget_id" {
  description = "ID of the AWS Budgets cost budget"
  value       = aws_budgets_budget.monthly.id
}
