resource "aws_cloudwatch_log_group" "harness" {
  name              = "/free-point/harness"
  retention_in_days = 7

  tags = {
    Name = "free-point-harness-logs"
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "free-point-dlq-depth"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alarm triggered when jobs dead-letter queue depth exceeds 0"

  dimensions = {
    QueueName = aws_sqs_queue.jobs_dlq.name
  }

  tags = {
    Name = "free-point-dlq-depth-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "free-point-alb-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alarm triggered when ALB target responds with 5xx errors"

  dimensions = {
    LoadBalancer = aws_lb.app_alb.arn_suffix
  }

  tags = {
    Name = "free-point-alb-5xx-alarm"
  }
}
