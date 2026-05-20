"""DO Spaces uploader — boto3 S3-compatible. Returns presigned URL (24h TTL)."""
from __future__ import annotations

import os
from datetime import datetime

import boto3
from botocore.client import Config

SIGNED_URL_TTL = 24 * 3600  # 24h


def _client():
    endpoint = os.environ.get("DO_SPACES_ENDPOINT", "").strip()
    region = os.environ.get("DO_SPACES_REGION", "nyc3").strip()
    key = os.environ.get("DO_SPACES_KEY", "").strip()
    secret = os.environ.get("DO_SPACES_SECRET", "").strip()
    if not (endpoint and key and secret):
        raise RuntimeError("DO_SPACES_ENDPOINT, DO_SPACES_KEY, DO_SPACES_SECRET required")
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4"),
    )


def _bucket() -> str:
    b = os.environ.get("DO_SPACES_BUCKET", "").strip()
    if not b:
        raise RuntimeError("DO_SPACES_BUCKET required")
    return b


def upload_xlsx(*, content: bytes, run_id: str, filename: str) -> str:
    """Upload bytes, return presigned URL valid 24h."""
    s3 = _client()
    bucket = _bucket()
    key = f"cut-orders/{run_id}/{filename}"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ContentDisposition=f'attachment; filename="{filename}"',
    )
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=SIGNED_URL_TTL,
    )
    return url


def regenerate_signed_url(*, run_id: str, filename: str) -> str:
    """Re-sign existing object (e.g. on /history click)."""
    s3 = _client()
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": f"cut-orders/{run_id}/{filename}"},
        ExpiresIn=SIGNED_URL_TTL,
    )
