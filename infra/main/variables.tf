variable "region" {
  type        = string
  description = "AWS region for deployment"
}

variable "domain_name" {
  type        = string
  description = "Fully qualified domain name for HTTPS application endpoint"
}

variable "route53_zone_id" {
  type        = string
  description = "Route 53 hosted zone ID for automatic DNS validation (null for manual DNS validation)"
  default     = null
  nullable    = true
}

variable "image_tag" {
  type        = string
  description = "Container image tag deployed by the autoscaling launch template"
}

variable "app_instance_type" {
  type        = string
  description = "EC2 instance type for harness application workers"
  default     = "t3.medium"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the custom VPC"
  default     = "10.20.0.0/16"
}

variable "asg_max" {
  type        = number
  description = "Maximum capacity for the application Auto Scaling Group"
  default     = 2
}
