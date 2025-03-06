import os
import json
import time
import uuid
import tempfile
from pathlib import Path
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from common.models import Base, Job, JobStatus, Chunk, ChunkStatus
from common.storage import download_file, upload_from_path
from silencewarp import split_video

# Initialize database connection
DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://user:password@localhost/video_processor"
)
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)

# Initialize Redis connection
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL)


def process_split_job(job_data):
    job_id = job_data["job_id"]
    input_path = job_data["input_path"]

    session = Session()
    job = session.query(Job).filter(Job.id == job_id).first()

    if not job:
        print(f"Job {job_id} not found in database")
        return

    # Update job status
    job.status = JobStatus.SPLITTING
    session.commit()

    try:
        # Create a temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download input file
            local_input_path = os.path.join(temp_dir, os.path.basename(input_path))
            download_file(input_path, local_input_path)

            # Split the video
            chunk_files = split_video(Path(local_input_path), temp_dir=temp_dir)

            # Upload chunks and create database entries
            for i, chunk_file in enumerate(chunk_files):
                chunk_id = str(uuid.uuid4())
                chunk_number = i + 1
                chunk_s3_path = f"chunks/{job_id}/chunk_{chunk_number:03d}.mp4"

                # Upload chunk to storage
                upload_from_path(str(chunk_file), chunk_s3_path)

                # Create chunk entry in database
                chunk = Chunk(
                    id=chunk_id,
                    job_id=job_id,
                    chunk_number=chunk_number,
                    input_path=chunk_s3_path,
                    status=ChunkStatus.PENDING,
                )
                session.add(chunk)

                # Queue chunk for processing
                redis_client.lpush(
                    "process_queue",
                    json.dumps(
                        {
                            "job_id": job_id,
                            "chunk_id": chunk_id,
                            "input_path": chunk_s3_path,
                        }
                    ),
                )

            session.commit()

            # Update job status
            job.status = JobStatus.PROCESSING
            session.commit()

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        session.commit()
        print(f"Error processing job {job_id}: {e}")


def run_worker():
    print("Starting splitter worker...")
    while True:
        # Block for 1 second, waiting for a job
        job_data = redis_client.brpop("split_queue", 1)

        if job_data:
            _, job_json = job_data
            job = json.loads(job_json)
            print(f"Processing split job: {job['job_id']}")
            process_split_job(job)

        # Small sleep to avoid busy-waiting
        time.sleep(0.1)


if __name__ == "__main__":
    run_worker()
