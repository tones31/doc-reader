import os
import boto3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# S3 env
DOCUMENT_BUCKET_ENDPOINT = os.getenv("DOCUMENT_BUCKET_ENDPOINT")
DOCUMENT_BUCKET_ACCESS_KEY_ID = os.getenv("DOCUMENT_BUCKET_ACCESS_KEY_ID")
DOCUMENT_BUCKET_SECRET_ACCESS_KEY = os.getenv("DOCUMENT_BUCKET_SECRET_ACCESS_KEY")
DOCUMENT_BUCKET_REGION = os.getenv("DOCUMENT_BUCKET_REGION")
DOCUMENT_BUCKET = os.getenv("DOCUMENT_BUCKET")

# Local env
DOCUMENT_LOCAL_DIR = os.getenv("DOCUMENT_LOCAL_DIR")

use_s3 = bool(DOCUMENT_BUCKET and DOCUMENT_BUCKET_ACCESS_KEY_ID and DOCUMENT_BUCKET_SECRET_ACCESS_KEY)
s3_client = None
upload_dir: Path | None = None


def get_s3_client():
    global s3_client
    if s3_client is None and use_s3:
        s3_client = boto3.client(
            "s3",
            endpoint_url=DOCUMENT_BUCKET_ENDPOINT or None,
            aws_access_key_id=DOCUMENT_BUCKET_ACCESS_KEY_ID,
            aws_secret_access_key=DOCUMENT_BUCKET_SECRET_ACCESS_KEY,
            region_name=DOCUMENT_BUCKET_REGION or "us-east-1",
        )
    return s3_client


# Get upload directory
def get_upload_dir() -> Path:
    global upload_dir
    if upload_dir is None:
        upload_dir = Path(DOCUMENT_LOCAL_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def is_s3() -> bool:
    return use_s3

# Save file to S3 or local storage
def save_file(key: str, content: bytes) -> None:
    if use_s3:
        client = get_s3_client()
        client.put_object(Bucket=DOCUMENT_BUCKET, Key=key, Body=content)
    else:
        path = get_upload_dir() / key
        path.write_bytes(content)


# Local file path
def get_file_path(key: str) -> Path | None:
    if use_s3:
        return None
    path = (get_upload_dir() / key).resolve()
    root = get_upload_dir().resolve()
    if not path.is_file():
        return None
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


# Presigned URL for S3
def get_presigned_url(key: str, expires_in: int = 3600) -> str | None:
    if not use_s3:
        return None
    client = get_s3_client()
    try:
        client.head_object(Bucket=DOCUMENT_BUCKET, Key=key)
    except Exception:
        return None
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": DOCUMENT_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )
    return url


def list_files() -> list[dict]:
    """Return list of stored files. Each item: {"name": str} for display and download query param."""
    if use_s3:
        client = get_s3_client()
        out: list[dict] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=DOCUMENT_BUCKET):
            for obj in page.get("Contents") or []:
                key = obj.get("Key")
                if key:
                    out.append({"name": key})
        return out
    root = get_upload_dir()
    out = []
    for path in root.iterdir():
        if path.is_file():
            out.append({"name": path.name})
    return sorted(out, key=lambda x: x["name"].lower())