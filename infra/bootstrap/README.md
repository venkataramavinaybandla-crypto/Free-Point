# Bootstrap Infrastructure

Foundational state storage and cost governance stack for Free Point.

## Resources Provisioned
- **S3 Bucket**: Versioned, encrypted (SSE-S3), public access blocked, `force_destroy = false` for remote Terraform state.
- **DynamoDB Table**: `PAY_PER_REQUEST` billing mode, `LockID` primary key for Terraform state locking.
- **AWS Budgets**: Monthly cost budget with automated email alerts triggered at 50%, 80%, and 100% threshold consumption.

## Required Variables (No Defaults)
- `bucket_name`: Globally unique S3 bucket name for state storage.
- `budget_amount`: Monthly budget amount limit in USD (e.g. "20.0").
- `budget_email`: Target email address for budget notifications.

## Exact Apply Commands

### 1. Initialize
```bash
terraform init
```

### 2. Plan
```bash
terraform plan \
  -var="bucket_name=<unique-s3-bucket-name>" \
  -var="budget_amount=<monthly-budget-usd>" \
  -var="budget_email=<notification-email>" \
  -out=bootstrap.tfplan
```

### 3. Apply
```bash
terraform apply bootstrap.tfplan
```

### 4. Cleanup Notes
This bootstrap stack is applied once and retained across development cycles to persist state and cost guardrails.
