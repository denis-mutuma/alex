#!/usr/bin/env python3
"""
Destroy the Alex Financial Advisor Part 7 infrastructure.
This script:
1. Empties the S3 bucket
2. Destroys infrastructure with Terraform
3. Cleans up local artifacts
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path


def terraform_state_params():
    bucket = os.environ.get("TF_STATE_BUCKET") or os.environ.get("ALEX_TERRAFORM_STATE_BUCKET")
    region = (
        os.environ.get("DEFAULT_AWS_REGION")
        or os.environ.get("AWS_REGION")
        or "us-east-1"
    )
    lock_table = os.environ.get("TF_STATE_LOCK_TABLE", "alex-terraform-locks")
    return bucket, region, lock_table


def run_command(cmd, cwd=None, check=True, capture_output=False, env=None):
    """Run a command and optionally capture output."""
    print(f"Running: {' '.join(cmd) if isinstance(cmd, list) else cmd}")

    if env is None:
        env = os.environ.copy()

    if capture_output:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=isinstance(cmd, str),
            env=env,
        )
        if check and result.returncode != 0:
            print(f"Error: {result.stderr}")
            return None
        return result.stdout.strip()
    else:
        result = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), env=env)
        if check and result.returncode != 0:
            sys.exit(1)
        return True


def terraform_init(terraform_dir):
    """Initialize Terraform with S3 backend (CI) or local state."""
    bucket, region, lock_table = terraform_state_params()
    tf_env = os.environ.copy()
    if bucket:
        tf_env.setdefault("TF_VAR_remote_state_bucket", bucket)

    init_cmd = ["terraform", "init", "-input=false"]
    if bucket:
        init_cmd.extend(
            [
                "-backend-config",
                f"bucket={bucket}",
                "-backend-config",
                "key=alex/7_frontend/terraform.tfstate",
                "-backend-config",
                f"region={region}",
                "-backend-config",
                f"dynamodb_table={lock_table}",
                "-backend-config",
                "encrypt=true",
                "-reconfigure",
            ]
        )
    else:
        init_cmd.append("-backend=false")

    run_command(init_cmd, cwd=terraform_dir, env=tf_env)
    return tf_env


def confirm_destruction():
    """Ask for confirmation before destroying resources."""
    print("⚠️  WARNING: This will destroy all Part 7 infrastructure!")
    print("This includes:")
    print("  - CloudFront distribution")
    print("  - API Gateway")
    print("  - Lambda function")
    print("  - S3 bucket and all contents")
    print("  - IAM roles and policies")
    print("")

    response = input("Are you sure you want to continue? Type 'yes' to confirm: ")
    return response.lower() == "yes"


def get_bucket_name(tf_env):
    """Get the S3 bucket name from Terraform output."""
    terraform_dir = Path(__file__).parent.parent / "terraform" / "7_frontend"

    if not terraform_dir.exists():
        print(f"  ❌ Terraform directory not found: {terraform_dir}")
        return None

    bucket_output = run_command(
        ["terraform", "output", "-raw", "s3_bucket_name"],
        cwd=terraform_dir,
        capture_output=True,
        check=False,
        env=tf_env,
    )

    return bucket_output if bucket_output else None


def empty_s3_bucket(bucket_name):
    """Empty the S3 bucket before deletion."""
    if not bucket_name:
        print("  ⚠️  No bucket name provided, skipping...")
        return

    print(f"\n🗑️  Emptying S3 bucket: {bucket_name}")

    # Check if bucket exists
    exists = run_command(
        ["aws", "s3", "ls", f"s3://{bucket_name}"],
        capture_output=True,
        check=False
    )

    if not exists:
        print(f"  Bucket {bucket_name} doesn't exist or is already empty")
        return

    # Delete all objects
    print(f"  Deleting all objects from {bucket_name}...")
    run_command([
        "aws", "s3", "rm",
        f"s3://{bucket_name}/",
        "--recursive"
    ])

    # Delete all versions (if versioning is enabled)
    print(f"  Deleting all object versions...")
    run_command([
        "aws", "s3api", "delete-objects",
        "--bucket", bucket_name,
        "--delete", "$(aws s3api list-object-versions --bucket " + bucket_name + " --output json --query='{Objects: Versions[].{Key:Key,VersionId:VersionId}}')"
    ], check=False)

    print(f"  ✅ Bucket {bucket_name} emptied")


def destroy_terraform(tf_env, auto_approve: bool):
    """Destroy infrastructure with Terraform."""
    print("\n🏗️  Destroying infrastructure with Terraform...")

    terraform_dir = Path(__file__).parent.parent / "terraform" / "7_frontend"

    if not terraform_dir.exists():
        print(f"  ❌ Terraform directory not found: {terraform_dir}")
        sys.exit(1)

    print("  Running terraform destroy...")
    cmd = ["terraform", "destroy", "-input=false"]
    if auto_approve:
        cmd.append("-auto-approve")

    run_command(cmd, cwd=terraform_dir, env=tf_env)
    print("  ✅ Infrastructure destroyed successfully")


def clean_local_artifacts():
    """Clean up local build artifacts."""
    print("\n🧹 Cleaning up local artifacts...")

    artifacts = [
        Path(__file__).parent.parent / "backend" / "api" / "api_lambda.zip",
        Path(__file__).parent.parent / "frontend" / "out",
        Path(__file__).parent.parent / "frontend" / ".next",
    ]

    for artifact in artifacts:
        if artifact.exists():
            if artifact.is_file():
                artifact.unlink()
                print(f"  Deleted: {artifact}")
            else:
                import shutil
                shutil.rmtree(artifact)
                print(f"  Deleted directory: {artifact}")

    print("  ✅ Local artifacts cleaned")


def main():
    """Main destruction function."""
    parser = argparse.ArgumentParser(description="Destroy Alex Part 7 Terraform stack")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (for CI). Still requires matching destroy workflow confirmation.",
    )
    args = parser.parse_args()

    print("💥 Alex Financial Advisor - Part 7 Infrastructure Destruction")
    print("=" * 60)

    if not args.yes and not confirm_destruction():
        print("\n❌ Destruction cancelled")
        sys.exit(0)

    terraform_dir = Path(__file__).parent.parent / "terraform" / "7_frontend"
    tf_env = terraform_init(terraform_dir)

    bucket_name = get_bucket_name(tf_env)

    if bucket_name:
        empty_s3_bucket(bucket_name)

    destroy_terraform(tf_env, auto_approve=args.yes)

    clean_local_artifacts()

    print("\n" + "=" * 60)
    print("✅ Destruction complete!")
    print("\nTo redeploy, run:")
    print("  uv run scripts/deploy.py")


if __name__ == "__main__":
    main()