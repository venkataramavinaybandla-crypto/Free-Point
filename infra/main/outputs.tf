output "alb_dns_name" {
  description = "Public DNS name of the Application Load Balancer"
  value       = aws_lb.app_alb.dns_name
}

output "ecr_repository_url" {
  description = "URL of the ECR repository for the harness image"
  value       = aws_ecr_repository.app.repository_url
}

output "acm_validation_records" {
  description = "DNS validation records for the ACM certificate"
  value = {
    for dvo in aws_acm_certificate.app_cert.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }
}

output "jobs_queue_url" {
  description = "URL of the jobs SQS queue"
  value       = aws_sqs_queue.jobs.url
}

output "jobs_dlq_url" {
  description = "URL of the jobs dead-letter queue"
  value       = aws_sqs_queue.jobs_dlq.url
}

output "jobs_table_name" {
  description = "Name of the DynamoDB jobs table"
  value       = aws_dynamodb_table.jobs.name
}

output "idempotency_table_name" {
  description = "Name of the DynamoDB idempotency table"
  value       = aws_dynamodb_table.idempotency.name
}
