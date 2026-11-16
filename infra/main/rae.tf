resource "aws_s3_bucket" "artifacts" {
  bucket        = "free-point-artifacts"
  force_destroy = true

  tags = {
    Name = "free-point-artifacts"
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudwatch_log_group" "rae" {
  name              = "/free-point/rae"
  retention_in_days = 7

  tags = {
    Name = "free-point-rae-logs"
  }
}

resource "aws_iam_role" "rae_instance_role" {
  name = "free-point-rae-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
      }
    ]
  })

  tags = {
    Name = "free-point-rae-instance-role"
  }
}

resource "aws_iam_policy" "rae" {
  name        = "free-point-rae-policy"
  description = "RAE only: artifacts read, results write, its own queue, no KMS or DynamoDB"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RAEArtifactsRead"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.artifacts.arn}/artifacts/*"
        ]
      },
      {
        Sid    = "RAEResultsWrite"
        Effect = "Allow"
        Action = ["s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.artifacts.arn}/results/*"
        ]
      },
      {
        Sid    = "RAEQueueOnly"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl"
        ]
        Resource = [
          aws_sqs_queue.rae.arn,
          aws_sqs_queue.rae_dlq.arn
        ]
      },
      {
        Sid      = "ECRAuthToken"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "ECRPullRAE"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = [aws_ecr_repository.rae.arn]
      },
      {
        Sid    = "CloudWatchLogsWrite"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = ["${aws_cloudwatch_log_group.rae.arn}:*"]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rae_attach" {
  role       = aws_iam_role.rae_instance_role.name
  policy_arn = aws_iam_policy.rae.arn
}

resource "aws_iam_instance_profile" "rae_profile" {
  name = "free-point-rae-instance-profile"
  role = aws_iam_role.rae_instance_role.name
}

resource "aws_security_group" "rae" {
  name        = "free-point-rae-sg"
  description = "RAE host: no inbound, egress only"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "DNS to the VPC resolver"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "Outbound to S3, SQS, ECR, and Git over HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "free-point-rae-sg"
  }
}

resource "aws_launch_template" "rae_lt" {
  name_prefix   = "free-point-rae-"
  image_id      = data.aws_ssm_parameter.al2023_ami.value
  instance_type = "t3.small"

  vpc_security_group_ids = [aws_security_group.rae.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.rae_profile.name
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

    dnf update -y
    dnf install -y docker git
    systemctl enable docker
    systemctl start docker
    systemctl start docker-user-firewall.service

    docker network create --subnet=172.30.0.0/24 free-point-untrusted || true
    docker volume create free-point-rae-work

    aws ecr get-login-password --region ${var.region} | docker login --username AWS --password-stdin ${aws_ecr_repository.rae.repository_url}
    docker pull ${aws_ecr_repository.rae.repository_url}:${var.image_tag}

    docker run -d --restart always --name rae-worker --user 0:0 \
      -v /var/run/docker.sock:/var/run/docker.sock:ro \
      -v free-point-rae-work:/rae-work \
      -e RAE_WORK_DIR=/rae-work \
      -e RAE_WORK_VOLUME=free-point-rae-work \
      -e AWS_REGION=${var.region} \
      -e RAE_QUEUE_URL=${aws_sqs_queue.rae.url} \
      -e RAE_ARTIFACTS_BUCKET=${aws_s3_bucket.artifacts.bucket} \
      -e RAE_ARTIFACTS_PREFIX=artifacts/ \
      -e RAE_RESULTS_PREFIX=results/ \
      -e RAE_SANDBOX_IMAGE=${aws_ecr_repository.rae.repository_url}:${var.image_tag} \
      ${aws_ecr_repository.rae.repository_url}:${var.image_tag} \
      python -m rae_mcp.worker
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "free-point-rae-worker"
    }
  }
}

resource "aws_autoscaling_group" "rae_asg" {
  name_prefix         = "free-point-rae-asg-"
  vpc_zone_identifier = [for s in aws_subnet.private : s.id]
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1

  launch_template {
    id      = aws_launch_template.rae_lt.id
    version = "$Latest"
  }

  lifecycle {
    create_before_destroy = true
  }
}
