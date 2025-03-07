import os
import boto3
from botocore.client import Config
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


# MinIO configuration
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET_NAME", "video-processor")
USE_SSL = os.environ.get("MINIO_USE_SSL", "false").lower() == "true"

# Initialize MinIO client
s3_client = boto3.client(
    "s3",
    endpoint_url=f"{'https' if USE_SSL else 'http'}://{MINIO_ENDPOINT}",
    aws_access_key_id=MINIO_ACCESS_KEY,
    aws_secret_access_key=MINIO_SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",  # This can be any AWS region for MinIO
)


def upload_file(file_obj, object_name):
    """Upload a file to MinIO"""
    s3_client.upload_fileobj(file_obj, MINIO_BUCKET, object_name)
    return f"s3://{MINIO_BUCKET}/{object_name}"


def download_file(object_name, file_path):
    """Download a file from MinIO"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    s3_client.download_file(MINIO_BUCKET, object_name, file_path)
    return file_path


def upload_from_path(local_path, object_name):
    """Upload a local file to MinIO"""
    with open(local_path, "rb") as file_data:
        s3_client.upload_fileobj(file_data, MINIO_BUCKET, object_name)
    return f"s3://{MINIO_BUCKET}/{object_name}"


def get_download_url(object_name, expires=3600):
    """Generate a presigned URL for downloading a file"""
    return s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": MINIO_BUCKET, "Key": object_name},
        ExpiresIn=expires,
    )
