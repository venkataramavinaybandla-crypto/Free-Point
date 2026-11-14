variable "aws_region" {
  type        = string
  description = "AWS region for provisioning bootstrap resources"
  default     = "ap-south-1"
}

variable "bucket_name" {
  type        = string
  description = "Name of the S3 bucket for Terraform remote state"
}

variable "budget_amount" {
  type        = string
  description = "Monthly budget limit amount in USD"
}

variable "budget_email" {
  type        = string
  description = "Email address to receive AWS budget threshold alerts"
}

variable "dynamodb_table_name" {
  type        = string
  description = "Name of the DynamoDB table for Terraform state locking"
  default     = "free-point-tf-state-locks"
}
