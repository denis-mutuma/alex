#!/usr/bin/env python3
"""
Destroy Alex Financial Advisor infrastructure (Terraform stacks 8 → 2).

Order: 8_enterprise → 7_frontend → 6_agents → 5_database → 4_researcher → 3_ingestion → 2_sagemaker
Does NOT destroy 0_bootstrap (remote state bucket, DynamoDB locks, GitHub Actions OIDC role).

Usage:
  uv run scripts/destroy.py              # interactive confirm
  uv run scripts/destroy.py --yes        # skip confirm (CI)
  uv run scripts/destroy.py --only 7_frontend
  uv run scripts/destroy.py --from 6_agents
  uv run scripts/destroy.py --keep-buckets   # skip S3 emptying (debug)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM_ROOT = REPO_ROOT / "terraform"

# Ordered for destruction (highest dependents first). 0_bootstrap is intentionally omitted.
STACKS: list[dict] = [
    {
        "name": "8_enterprise",
        "backend": "local",
        "s3_state_key": None,
        "bucket_outputs": [],
    },
    {
        "name": "7_frontend",
        "backend": "s3",
        "s3_state_key": "alex/7_frontend/terraform.tfstate",
        "bucket_outputs": ["s3_bucket_name"],
    },
    {
        "name": "6_agents",
        "backend": "s3",
        "s3_state_key": "alex/6_agents/terraform.tfstate",
        "bucket_outputs": ["lambda_packages_bucket"],
    },
    {
        "name": "5_database",
        "backend": "s3",
        "s3_state_key": "alex/5_database/terraform.tfstate",
        "bucket_outputs": [],
    },
    {
        "name": "4_researcher",
        "backend": "local",
        "s3_state_key": None,
        "bucket_outputs": [],
    },
    {
        "name": "3_ingestion",
        "backend": "local",
        "s3_state_key": None,
        # Part 3 output is vector_bucket_name (not vectors_bucket_name)
        "bucket_outputs": ["vector_bucket_name"],
    },
    {
        "name": "2_sagemaker",
        "backend": "local",
        "s3_state_key": None,
        "bucket_outputs": [],
    },
]


def terraform_state_params():
    bucket = os.environ.get("TF_STATE_BUCKET") or os.environ.get("ALEX_TERRAFORM_STATE_BUCKET")
    region = (
        os.environ.get("DEFAULT_AWS_REGION")
        or os.environ.get("AWS_REGION")
        or "us-east-1"
    )
    lock_table = os.environ.get("TF_STATE_LOCK_TABLE", "alex-terraform-locks")
    return bucket, region, lock_table


def load_dotenv_file(path: Path) -> None:
    """Merge .env into os.environ without overriding existing keys."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        os.environ[key] = val


def run_command(cmd, cwd=None, check=True, capture_output=False, env=None):
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
            print(result.stderr or result.stdout or "", file=sys.stderr)
            return None
        return (result.stdout or "").strip()
    result = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), env=env)
    if check and result.returncode != 0:
        sys.exit(1)
    return True


def confirm_destruction(only: str | None, from_stack: str | None) -> bool:
    print("NOT destroyed: 0_bootstrap (Terraform state bucket, DynamoDB lock table, GitHub OIDC role).")
    print("")
    if only:
        print(f"WARNING: Destroying ONLY stack {only!r}.")
    elif from_stack:
        print(
            f"WARNING: Destroying from {from_stack!r} through 2_sagemaker "
            "(reverse dependency order)."
        )
    else:
        print("WARNING: This will destroy all Alex workload stacks (8_enterprise through 2_sagemaker).")
        print("")
        print("Includes (when deployed): CloudWatch dashboards, CloudFront, API Gateway, Lambdas,")
        print("SQS, Aurora, App Runner, ingestion API, SageMaker endpoint, S3 buckets, IAM roles, etc.")
    print("")
    print("Note: Destroying 7_frontend can take 15–30+ minutes while CloudFront disables.")
    print("")
    response = input("Are you sure? Type 'yes' to confirm: ")
    return response.strip().lower() == "yes"


def terraform_init(terraform_dir: Path, stack: dict, tf_env: dict) -> None:
    bucket, region, lock_table = terraform_state_params()
    init_cmd = ["terraform", "init", "-input=false", "-reconfigure"]

    want_s3 = stack["backend"] == "s3" and stack.get("s3_state_key")
    if want_s3 and bucket:
        init_cmd.extend(
            [
                "-backend-config",
                f"bucket={bucket}",
                "-backend-config",
                f"key={stack['s3_state_key']}",
                "-backend-config",
                f"region={region}",
                "-backend-config",
                f"dynamodb_table={lock_table}",
                "-backend-config",
                "encrypt=true",
            ]
        )
        if bucket:
            tf_env.setdefault("TF_VAR_remote_state_bucket", bucket)
    else:
        if want_s3 and not bucket:
            print(
                "  WARNING: TF_STATE_BUCKET / ALEX_TERRAFORM_STATE_BUCKET not set; "
                f"initializing {stack['name']} with -backend=false (local state only)."
            )
        init_cmd.append("-backend=false")

    run_command(init_cmd, cwd=str(terraform_dir), env=tf_env)


def terraform_output_raw(terraform_dir: Path, name: str, tf_env: dict) -> str | None:
    out = run_command(
        ["terraform", "output", "-raw", name],
        cwd=str(terraform_dir),
        capture_output=True,
        check=False,
        env=tf_env,
    )
    return out if out else None


def _s3_bucket_exists(bucket_name: str) -> bool:
    r = subprocess.run(
        ["aws", "s3api", "head-bucket", "--bucket", bucket_name],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def empty_s3_bucket(bucket_name: str) -> None:
    """Remove all objects, versions, and delete markers so Terraform can delete the bucket."""
    print(f"  Emptying S3 bucket: {bucket_name}")

    if not _s3_bucket_exists(bucket_name):
        print(f"  Bucket does not exist or is not accessible: {bucket_name} (skipping empty)")
        return

    run_command(
        ["aws", "s3", "rm", f"s3://{bucket_name}/", "--recursive"],
        check=False,
    )

    key_marker = None
    version_marker = None
    while True:
        cmd = [
            "aws",
            "s3api",
            "list-object-versions",
            "--bucket",
            bucket_name,
            "--output",
            "json",
        ]
        if key_marker:
            cmd.extend(["--key-marker", key_marker])
        if version_marker:
            cmd.extend(["--version-id-marker", version_marker])

        raw = run_command(cmd, capture_output=True, check=False)
        if not raw:
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print("  Warning: could not parse list-object-versions output", file=sys.stderr)
            break

        to_delete: list[dict] = []
        for v in data.get("Versions") or []:
            if "Key" in v and "VersionId" in v:
                to_delete.append({"Key": v["Key"], "VersionId": v["VersionId"]})
        for d in data.get("DeleteMarkers") or []:
            if "Key" in d and "VersionId" in d:
                to_delete.append({"Key": d["Key"], "VersionId": d["VersionId"]})

        batch_size = 1000
        for i in range(0, len(to_delete), batch_size):
            chunk = to_delete[i : i + batch_size]
            payload = {"Objects": chunk, "Quiet": True}
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(payload, tmp)
                tmp_path = tmp.name
            try:
                run_command(
                    [
                        "aws",
                        "s3api",
                        "delete-objects",
                        "--bucket",
                        bucket_name,
                        "--delete",
                        f"file://{tmp_path}",
                    ],
                    check=False,
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        if not data.get("IsTruncated"):
            break
        key_marker = data.get("NextKeyMarker")
        version_marker = data.get("NextVersionIdMarker")
        if key_marker is None and version_marker is None:
            break

    print(f"  Done emptying: {bucket_name}")


def destroy_stack(terraform_dir: Path, auto_approve: bool, tf_env: dict) -> None:
    cmd = ["terraform", "destroy", "-input=false"]
    if auto_approve:
        cmd.append("-auto-approve")
    run_command(cmd, cwd=str(terraform_dir), env=tf_env)


def stack_indices_for_run(only: str | None, from_stack: str | None) -> list[int]:
    names = [s["name"] for s in STACKS]
    if only and from_stack:
        print("Error: use only one of --only or --from", file=sys.stderr)
        sys.exit(2)
    if only:
        if only not in names:
            print(f"Error: unknown stack {only!r}. Valid: {names}", file=sys.stderr)
            sys.exit(2)
        return [names.index(only)]
    if from_stack:
        if from_stack not in names:
            print(f"Error: unknown stack {from_stack!r}. Valid: {names}", file=sys.stderr)
            sys.exit(2)
        start = names.index(from_stack)
        return list(range(start, len(STACKS)))
    return list(range(len(STACKS)))


def print_bootstrap_preserved_summary() -> None:
    bucket, region, lock_table = terraform_state_params()
    acct = run_command(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        capture_output=True,
        check=False,
    )
    print("")
    print("=" * 60)
    print("0_bootstrap preserved (not destroyed)")
    print("=" * 60)
    if acct:
        default_bucket = f"alex-terraform-state-{acct.strip()}"
        print(f"  Typical state bucket: {bucket or default_bucket}")
    else:
        print(f"  State bucket (if set): {bucket or '(set TF_STATE_BUCKET to know name)'}")
    print(f"  DynamoDB lock table: {lock_table}  (region: {region})")
    print("  IAM role (from bootstrap): github-actions-alex-deploy")
    print("  OIDC provider: token.actions.githubusercontent.com (if created by bootstrap)")
    print("")
    print("To remove bootstrap later: cd terraform/0_bootstrap && terraform destroy")
    print("To wipe state objects only: empty the state bucket prefix alex/ after all stacks are gone.")


def clean_local_artifacts() -> None:
    print("\nCleaning up local build artifacts (optional)...")
    artifacts = [
        REPO_ROOT / "backend" / "api" / "api_lambda.zip",
        REPO_ROOT / "frontend" / "out",
        REPO_ROOT / "frontend" / ".next",
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Destroy Alex Terraform stacks (8 → 2)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--only",
        metavar="STACK",
        help="Destroy only this stack (e.g. 7_frontend)",
    )
    parser.add_argument(
        "--from",
        dest="from_stack",
        metavar="STACK",
        help="Destroy this stack and all later stacks in the destroy order (e.g. 6_agents → … → 2_sagemaker)",
    )
    parser.add_argument(
        "--keep-buckets",
        action="store_true",
        help="Skip emptying S3 buckets before terraform destroy (debug)",
    )
    parser.add_argument(
        "--skip-local-artifacts",
        action="store_true",
        help="Do not remove local frontend/out, .next, api_lambda.zip",
    )
    args = parser.parse_args()

    load_dotenv_file(REPO_ROOT / ".env")

    print("Alex Financial Advisor — destroy workload stacks (8 → 2)")
    print("=" * 60)

    if not args.yes and not confirm_destruction(args.only, args.from_stack):
        print("\nDestruction cancelled.")
        sys.exit(0)

    indices = stack_indices_for_run(args.only, args.from_stack)
    bucket, region, _lock = terraform_state_params()
    tf_env = os.environ.copy()
    if bucket:
        tf_env.setdefault("TF_VAR_remote_state_bucket", bucket)

    for idx in indices:
        stack = STACKS[idx]
        name = stack["name"]
        terraform_dir = TERRAFORM_ROOT / name
        if not terraform_dir.is_dir():
            print(f"\nSkipping {name}: directory not found: {terraform_dir}")
            continue

        print(f"\n--- Stack: {name} ---")
        if name == "7_frontend":
            print("  (CloudFront teardown can take 15–30+ minutes; Terraform will wait.)")

        terraform_init(terraform_dir, stack, tf_env)

        if not args.keep_buckets and stack["bucket_outputs"]:
            for out_name in stack["bucket_outputs"]:
                b = terraform_output_raw(terraform_dir, out_name, tf_env)
                if b:
                    empty_s3_bucket(b)
                else:
                    print(f"  No terraform output {out_name!r} (bucket empty/skip or no state)")

        destroy_stack(terraform_dir, auto_approve=args.yes, tf_env=tf_env)
        print(f"  Destroyed: {name}")

    if not args.skip_local_artifacts:
        clean_local_artifacts()

    print("\n" + "=" * 60)
    print("Workload destruction complete.")
    print("Redeploy: apply stacks in order 2 → 8 (see terraform/README.md).")
    print_bootstrap_preserved_summary()


if __name__ == "__main__":
    main()
