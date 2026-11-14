# Free Point Main Infrastructure Stack

This Terraform configuration deploys the production walking skeleton for Free Point:
- Custom VPC across 2 Availability Zones with 2 public and 2 private subnets.
- S3 and DynamoDB gateway endpoints (no expensive interface endpoints).
- Lightweight NAT instance (`t3.nano` running AL2023 with persisted IP forwarding and MASQUERADE) providing egress for private subnets.
- Internet-facing Application Load Balancer with TLS 1.2+ certificate enforcement and HTTP-to-HTTPS redirect.
- Auto Scaling Group (1 to `asg_max` instances) deploying the containerized harness from ECR using IMDSv2 (hop limit 2) and instance refresh.
- Job queueing via SQS with dead-letter queue, DynamoDB tables with on-demand pricing and TTL, and CloudWatch metrics/alarms.
- Least-privilege IAM instance role strictly bounded to application resources.

> [!WARNING]
> **Cost Warning:** The Application Load Balancer, NAT instance, and EC2 Auto Scaling instances incur hourly charges while running. To keep project costs minimal, always run `terraform destroy` at the end of each session.

## Prerequisites

1. Bootstrap stack deployed (`infra/bootstrap`) to establish the remote state bucket and DynamoDB lock table.
2. An ECR image built and pushed with a valid tag.
3. Configured AWS CLI credentials with administrator permissions.

## Exact Deployment Commands

### 1. Initialize Backend

Initialize Terraform with the remote state backend configured during bootstrap:

```bash
terraform init \
  -backend-config="bucket=<STATE_BUCKET_NAME>" \
  -backend-config="key=main/terraform.tfstate" \
  -backend-config="region=<AWS_REGION>" \
  -backend-config="dynamodb_table=<LOCK_TABLE_NAME>"
```

### 2. Plan Execution

Generate an execution plan. If `route53_zone_id` is omitted or `null`, ACM DNS validation records will be output for manual creation in your external DNS provider:

```bash
terraform plan \
  -var="region=ap-south-1" \
  -var="domain_name=harness.example.com" \
  -var="image_tag=v0.1.0" \
  -out=main.tfplan
```

### 3. Apply Plan

Apply the verified execution plan:

```bash
terraform apply main.tfplan
```

### 4. Teardown Infrastructure

Destroy all resources immediately after evaluation or testing to prevent unnecessary charges:

```bash
terraform destroy \
  -var="region=ap-south-1" \
  -var="domain_name=harness.example.com" \
  -var="image_tag=v0.1.0"
```
