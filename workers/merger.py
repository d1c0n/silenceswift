import os
import json
import time
import tempfile
from pathlib import Path
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from common.models import Base, Job, JobStatus, Chunk, ChunkStatus
from common.storage import download_file, upload_from_path
from silencewarp import merge_chunks

# Initialize database and Redis connections
DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://user:password@localhost/video_processor"
)
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL)


def process_merge_job(job_data):
    job_id = job_data["job_id"]

    session = Session()
    job = session.query(Job).filter(Job.id == job_id).first()

    if not job:
        print(f"Job {job_id} not found in database")
        return

    try:
        # Get all chunks for this job
        chunks = (
            session.query(Chunk)
            .filter(Chunk.job_id == job_id, Chunk.status == ChunkStatus.COMPLETED)
            .order_by(Chunk.chunk_number)
            .all()
        )

        if not chunks:
            raise ValueError("No completed chunks found for merging")

        # Create a temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download all chunks
            local_chunk_paths = []
            for chunk in chunks:
                local_chunk_path = os.path.join(
                    temp_dir, os.path.basename(chunk.output_path)
                )
                download_file(chunk.output_path, local_chunk_path)
                local_chunk_paths.append(Path(local_chunk_path))

            # Prepare output path
            output_filename = os.path.basename(job.input_path)
            local_output_path = os.path.join(temp_dir, f"output_{output_filename}")

            # Merge chunks
            merge_chunks(local_chunk_paths, Path(local_output_path))

            # Upload merged video
            output_s3_path = f"output/{job_id}/{output_filename}"
            upload_from_path(local_output_path, output_s3_path)

            # Update job status
            job.output_path = output_s3_path
            job.status = JobStatus.COMPLETED
            session.commit()

            print(f"Job {job_id} completed successfully")

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        session.commit()
        print(f"Error merging job {job_id}: {e}")


def run_worker():
    print("Starting merger worker...")
    while True:
        # Block for 1 second, waiting for a job
        job_data = redis_client.brpop("merge_queue", 1)

        if job_data:
            _, job_json = job_data
            job = json.loads(job_json)
            print(f"Processing merge job: {job['job_id']}")
            process_merge_job(job)

        # Small sleep to avoid busy-waiting
        time.sleep(0.1)


if __name__ == "__main__":
    run_worker()
