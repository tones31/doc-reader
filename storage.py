"""
File storage abstraction: local disk or S3-compatible.
Uses S3 when BUCKET (and credentials) are set in env; otherwise uses UPLOAD_DIR.
"""
import os
import boto3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# S3 env
ENDPOINT = os.getenv("ENDPOINT")
ACCESS_KEY_ID = os.getenv("ACCESS_KEY_ID")
SECRET_ACCESS_KEY = os.getenv("SECRET_ACCESS_KEY")
REGION = os.getenv("REGION")
BUCKET = os.getenv("BUCKET")

# Local env
UPLOAD_DIR_ENV = os.getenv("UPLOAD_DIR", "document_storage")

use_s3 = bool(BUCKET and ACCESS_KEY_ID and SECRET_ACCESS_KEY)
s3_client = None
upload_dir: Path | None = None


def _gets3_client():
    global s3_client
    if s3_client is None and use_s3:
        s3_client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT or None,
            aws_access_key_id=ACCESS_KEY_ID,
            aws_secret_access_key=SECRET_ACCESS_KEY,
            region_name=REGION or "us-east-1",
        )
    return s3_client


def _getupload_dir() -> Path:
    global upload_dir
    if upload_dir is None:
        upload_dir = Path(UPLOAD_DIR_ENV)
        upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def is_s3() -> bool:
    return use_s3

# Save file to S3 or local storage
def save_file(key: str, content: bytes) -> None:
    if use_s3:
        client = _gets3_client()
        client.put_object(Bucket=BUCKET, Key=key, Body=content)
    else:
        path = _getupload_dir() / key
        path.write_bytes(content)


# Local file path
def get_file_path(key: str) -> Path | None:
    if use_s3:
        return None
    path = (_getupload_dir() / key).resolve()
    root = _getupload_dir().resolve()
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
    client = _gets3_client()
    try:
        client.head_object(Bucket=BUCKET, Key=key)
    except Exception:
        return None
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )
    return url
