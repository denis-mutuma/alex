variable "aws_region" {
  description = "Region for the Terraform provider (S3 state bucket is regional)"
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the deploy role (owner/repo, no URL prefix)"
  type        = string
}
