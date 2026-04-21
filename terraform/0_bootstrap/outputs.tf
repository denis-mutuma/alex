output "terraform_state_bucket" {
  description = "S3 bucket name for Terraform remote state (TF_STATE_BUCKET / backend init)"
  value       = aws_s3_bucket.terraform_state.id
}

output "terraform_locks_table" {
  description = "DynamoDB table used for state locking"
  value       = aws_dynamodb_table.terraform_locks.name
}

output "github_actions_role_arn" {
  description = "Add this to GitHub Actions secret AWS_ROLE_ARN"
  value       = aws_iam_role.github_actions.arn
}
