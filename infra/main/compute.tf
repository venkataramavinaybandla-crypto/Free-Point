resource "aws_launch_template" "app_lt" {
  name_prefix   = "free-point-app-"
  image_id      = data.aws_ssm_parameter.al2023_ami.value
  instance_type = var.app_instance_type

  vpc_security_group_ids = [aws_security_group.app.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.app_profile.name
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    set -xe

    # 1. Persisted systemd unit to add DOCKER-USER rule dropping 169.254.169.254 for untrusted container subnet (172.30.0.0/24)
    cat <<'UNIT' > /etc/systemd/system/docker-user-firewall.service
    [Unit]
    Description=DOCKER-USER rule dropping IMDS metadata for untrusted container subnet
    After=docker.service
    Requires=docker.service

    [Service]
    Type=oneshot
    ExecStart=/bin/sh -c 'iptables -C DOCKER-USER -s 172.30.0.0/24 -d 169.254.169.254 -j DROP 2>/dev/null || iptables -I DOCKER-USER -s 172.30.0.0/24 -d 169.254.169.254 -j DROP'
    RemainAfterExit=yes

    [Install]
    WantedBy=multi-user.target
    UNIT

    systemctl daemon-reload
    systemctl enable docker-user-firewall.service

    # 2. Install Docker and Docker Compose plugin on Amazon Linux 2023
    dnf update -y
    dnf install -y docker
    systemctl enable docker
    systemctl start docker
    systemctl start docker-user-firewall.service

    mkdir -p /usr/local/lib/docker/cli-plugins
    COMPOSE_VERSION="v2.24.5"
    curl -SL "https://github.com/docker/compose/releases/download/$${COMPOSE_VERSION}/docker-compose-linux-x86_64" -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

    # 3. Authenticate with ECR and pull image
    aws ecr get-login-password --region ${var.region} | docker login --username AWS --password-stdin ${aws_ecr_repository.app.repository_url}

    # 4. Generate docker-compose.yml and start container
    mkdir -p /opt/free-point
    cat <<COMPOSE > /opt/free-point/docker-compose.yml
    services:
      harness:
        image: ${aws_ecr_repository.app.repository_url}:${var.image_tag}
        restart: always
        ports:
          - "8000:8000"
        environment:
          - AWS_REGION=${var.region}
          - DYNAMODB_TABLE_JOBS=${aws_dynamodb_table.jobs.name}
          - DYNAMODB_TABLE_IDEMPOTENCY=${aws_dynamodb_table.idempotency.name}
          - SQS_QUEUE_JOBS_URL=${aws_sqs_queue.jobs.url}
          - SQS_QUEUE_JOBS_DLQ_URL=${aws_sqs_queue.jobs_dlq.url}
    COMPOSE

    cd /opt/free-point
    docker compose pull
    docker compose up -d
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "free-point-worker"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "app_asg" {
  name_prefix         = "free-point-asg-"
  vpc_zone_identifier = [for s in aws_subnet.private : s.id]
  target_group_arns   = [aws_lb_target_group.app_tg.arn]

  min_size         = 1
  max_size         = var.asg_max
  desired_capacity = 1

  health_check_type         = "ELB"
  health_check_grace_period = 300

  launch_template {
    id      = aws_launch_template.app_lt.id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
      instance_warmup        = 300
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_policy" "queue_backlog_tracking" {
  name                   = "free-point-jobs-backlog-scaling"
  autoscaling_group_name = aws_autoscaling_group.app_asg.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    target_value = 10.0

    customized_metric_specification {
      metrics {
        label       = "BacklogPerInstance"
        id          = "e1"
        expression  = "m1 / m2"
        return_data = true
      }

      metrics {
        id = "m1"
        metric_stat {
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesVisible"
            dimensions {
              name  = "QueueName"
              value = aws_sqs_queue.jobs.name
            }
          }
          stat = "Sum"
        }
        return_data = false
      }

      metrics {
        id = "m2"
        metric_stat {
          metric {
            namespace   = "AWS/AutoScaling"
            metric_name = "GroupInServiceInstances"
            dimensions {
              name  = "AutoScalingGroupName"
              value = aws_autoscaling_group.app_asg.name
            }
          }
          stat = "Average"
        }
        return_data = false
      }
    }
  }
}
