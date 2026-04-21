variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "remote_state_bucket" {
  description = "S3 bucket holding Part 5 and Part 6 remote state (same keys as CI). Empty = read ../5_database and ../6_agents local .tfstate files"
  type        = string
  default     = ""
}

# Clerk validation happens in Lambda, not at API Gateway level
variable "clerk_jwks_url" {
  description = "Clerk JWKS URL for JWT validation in Lambda"
  type        = string
}

variable "clerk_issuer" {
  description = "Clerk issuer URL (kept for Lambda environment)"
  type        = string
  default     = "" # Not actually used but kept for backwards compatibility
}