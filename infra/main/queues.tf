resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "free-point-jobs-dlq"
  message_retention_seconds = 1209600

  tags = {
    Name = "free-point-jobs-dlq"
  }
}

resource "aws_sqs_queue" "jobs" {
  name                       = "free-point-jobs"
  visibility_timeout_seconds = 180

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 2
  })

  tags = {
    Name = "free-point-jobs"
  }
}
