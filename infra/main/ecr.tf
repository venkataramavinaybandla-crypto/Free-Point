resource "aws_ecr_repository" "rae" {
  name                 = "free-point/rae"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "free-point-rae-ecr"
  }
}

resource "aws_ecr_repository" "app" {
  name                 = "free-point/harness"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "free-point-harness-ecr"
  }
}
