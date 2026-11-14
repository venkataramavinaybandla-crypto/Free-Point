resource "aws_security_group" "alb" {
  name        = "free-point-alb-sg"
  description = "Public HTTP and HTTPS ingress for Application Load Balancer"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Inbound HTTP from internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Inbound HTTPS from internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Outbound to app targets"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "free-point-alb-sg"
  }
}

resource "aws_security_group" "app" {
  name        = "free-point-app-sg"
  description = "Security group for private app instances"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Inbound port 8000 from ALB security group only"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Outbound traffic to internet via NAT and VPC endpoints"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "free-point-app-sg"
  }
}

resource "aws_security_group" "nat" {
  name        = "free-point-nat-sg"
  description = "Ingress from private subnets for NAT instance outbound translation"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Inbound traffic from private subnets"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [for s in aws_subnet.private : s.cidr_block]
  }

  egress {
    description = "Outbound traffic to internet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "free-point-nat-sg"
  }
}
