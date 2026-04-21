# CI/CD bootstrap (S3 state + GitHub Actions role)

Run **once** on your machine with your normal IAM user (`aiengineer`).

## What this creates

- S3 bucket `alex-terraform-state-<account_id>`
- DynamoDB table `alex-terraform-locks` (state locking)
- IAM OIDC provider for GitHub Actions + role `github-actions-alex-deploy`

## Apply

```bash
cd terraform/0_bootstrap
cp terraform.tfvars.example terraform.tfvars
# Edit: github_repository = "your-github-login/alex"

terraform init
terraform apply
```

Note the outputs: `terraform_state_bucket`, `github_actions_role_arn`.

## GitHub repository secrets

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|--------|--------|
| `AWS_ROLE_ARN` | Output `github_actions_role_arn` |
| `AWS_ACCOUNT_ID` | Your 12-digit account ID |
| `DEFAULT_AWS_REGION` | e.g. `us-east-1` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk publishable key (frontend build) |
| `TF_VAR_clerk_jwks_url` | Same as `clerk_jwks_url` in `terraform/7_frontend` / Clerk dashboard |
| `TF_VAR_clerk_issuer` | Same as `clerk_issuer` in Part 7 Terraform |

`TF_STATE_BUCKET` in workflows is derived as `alex-terraform-state-<AWS_ACCOUNT_ID>` (must match the bootstrap bucket name).

## Migrate Part 5 / Part 6 / Part 7 state into S3 (required before CI)

CI expects these object keys in the state bucket:

- `alex/5_database/terraform.tfstate`
- `alex/6_agents/terraform.tfstate`
- `alex/7_frontend/terraform.tfstate`

From each directory, after `0_bootstrap` exists:

```bash
cd terraform/5_database
terraform init -backend=false   # if you still use local state only
terraform init -migrate-state   # when adding remote — follow prompts, or use:
# terraform init -backend-config="bucket=alex-terraform-state-ACCOUNTID" -backend-config="key=alex/5_database/terraform.tfstate" -backend-config="region=us-east-1" -backend-config="dynamodb_table=alex-terraform-locks" -backend-config="encrypt=true" -migrate-state
```

Repeat for `6_agents` and `7_frontend` with the correct `key=` path.

Set `remote_state_bucket = "alex-terraform-state-<account_id>"` in `terraform/7_frontend/terraform.tfvars` for local runs that should read Part 5/6 from S3 (optional; empty keeps using `../5_database` and `../6_agents` local files).

## Existing OIDC provider

If AWS already has `token.actions.githubusercontent.com`, import before apply:

```bash
terraform import aws_iam_openid_connect_provider.github \
  arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com
```

Then apply only the IAM role and attachments (or adjust the config to match your existing provider).
