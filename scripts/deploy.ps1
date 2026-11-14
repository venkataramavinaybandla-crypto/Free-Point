[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Image tag to build and push")]
    [string]$ImageTag,

    [Parameter(Mandatory = $true, HelpMessage = "ECR Repository URL (e.g. 123456789012.dkr.ecr.region.amazonaws.com/free-point/harness)")]
    [string]$EcrRepoUrl,

    [Parameter(Mandatory = $true, HelpMessage = "AWS Region (e.g. ap-south-1)")]
    [string]$AwsRegion,

    [Parameter(Mandatory = $true, HelpMessage = "Auto Scaling Group Name")]
    [string]$AsgName
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$HarnessDir = Join-Path $RepoRoot "harness"
$FullImageUri = "$EcrRepoUrl`:$ImageTag"

Write-Host "==> Building harness Docker image for linux/amd64: $FullImageUri" -ForegroundColor Cyan
docker build --platform linux/amd64 -t $FullImageUri -f (Join-Path $HarnessDir "Dockerfile") $HarnessDir

Write-Host "==> Authenticating Docker to ECR registry in region $AwsRegion" -ForegroundColor Cyan
$Password = aws ecr get-login-password --region $AwsRegion
$Password | docker login --username AWS --password-stdin $EcrRepoUrl

Write-Host "==> Pushing image to ECR: $FullImageUri" -ForegroundColor Cyan
docker push $FullImageUri

Write-Host "==> Triggering Auto Scaling Group instance refresh for $AsgName" -ForegroundColor Cyan
aws autoscaling start-instance-refresh `
    --auto-scaling-group-name $AsgName `
    --region $AwsRegion `
    --preferences '{"MinHealthyPercentage": 50, "InstanceWarmup": 300}'

Write-Host "==> Deployment initiated successfully for image tag $ImageTag" -ForegroundColor Green
