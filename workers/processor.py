import os
import json
import time
import tempfile
from pathlib import Path
import redis
from sqlalchemy import create_engine, and_
from sqlalchemy.orm import sessionmaker
from common.models import Base, Job, JobStatus, Chunk, ChunkStatus
from common.storage import download_file, upload_from_path
from silencewarp import (
    calculate_noise_threshold_ebur128,
    get_frame_rate,
    detect_silence,
    create_ffmpeg_speedup_filter,
    _run_ffmpeg_command,
)
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize database and Redis connections (similar to splitter.py)
DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://user:password@localhost/video_processor"
)
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL)

# Processing parameters
SILENCE_PERCENTILE = float(os.environ.get("SILENCE_PERCENTILE", "30.0"))
SILENCE_DURATION = float(os.environ.get("SILENCE_DURATION", "0.35"))
SPEED_FACTOR = float(os.environ.get("SPEED_FACTOR", "10.0"))


def process_chunk(job_data):
    job_id = job_data["job_id"]
    chunk_id = job_data["chunk_id"]
    input_path = job_data["input_path"]

    session = Session()
    chunk = session.query(Chunk).filter(Chunk.id == chunk_id).first()

    if not chunk:
        print(f"Chunk {chunk_id} not found in database")
        return

    # Update chunk status
    chunk.status = ChunkStatus.PROCESSING
    session.commit()

    try:
        # Create a temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download input chunk
            local_input_path = os.path.join(temp_dir, os.path.basename(input_path))
            download_file(input_path, local_input_path)
            local_input_path = Path(local_input_path)

            # Calculate noise threshold
            noise_threshold = calculate_noise_threshold_ebur128(
                local_input_path, percentile=SILENCE_PERCENTILE
            )

            if noise_threshold is not None:
                # Get frame rate
                fps = get_frame_rate(local_input_path)

                # Detect silence
                silence = detect_silence(
                    local_input_path,
                    noise_threshold,
                    silence_duration=SILENCE_DURATION,
                    fps=fps,
                )

                if silence:
                    # Create filter complex
                    filter_complex = create_ffmpeg_speedup_filter(
                        silence, speed_factor=SPEED_FACTOR, fps=fps
                    )

                    # Create a temporary file for the processed chunk
                    local_output_path = os.path.join(
                        temp_dir, f"processed_{os.path.basename(input_path)}"
                    )

                    # Create a temporary file for the filter complex
                    filter_complex_file = os.path.join(
                        temp_dir, f"filter_{os.path.basename(input_path)}.txt"
                    )
                    with open(filter_complex_file, "w") as f:
                        f.write(filter_complex)

                    # Apply the filter complex
                    command = [
                        "ffmpeg",
                        "-i",
                        str(local_input_path),
                        "-filter_complex_script",
                        filter_complex_file,
                        "-map",
                        "[outv]",
                        "-map",
                        "[outa]",
                        local_output_path,
                    ]
                    _run_ffmpeg_command(command)

                    # Upload processed chunk
                    output_s3_path = (
                        f"processed/{job_id}/chunk_{chunk.chunk_number:03d}.mp4"
                    )
                    upload_from_path(local_output_path, output_s3_path)

                    # Update chunk status
                    chunk.output_path = output_s3_path
                    chunk.status = ChunkStatus.COMPLETED
                    session.commit()
                else:
                    # No silence to process, just pass the chunk through
                    output_s3_path = (
                        f"processed/{job_id}/chunk_{chunk.chunk_number:03d}.mp4"
                    )
                    upload_from_path(str(local_input_path), output_s3_path)

                    # Update chunk status
                    chunk.output_path = output_s3_path
                    chunk.status = ChunkStatus.COMPLETED
                    session.commit()
            else:
                # No audio or couldn't calculate threshold, just pass the chunk through
                output_s3_path = (
                    f"processed/{job_id}/chunk_{chunk.chunk_number:03d}.mp4"
                )
                upload_from_path(str(local_input_path), output_s3_path)

                # Update chunk status
                chunk.output_path = output_s3_path
                chunk.status = ChunkStatus.COMPLETED
                session.commit()

        # Check if all chunks for this job are processed
        total_chunks = session.query(Chunk).filter(Chunk.job_id == job_id).count()
        completed_chunks = (
            session.query(Chunk)
            .filter(and_(Chunk.job_id == job_id, Chunk.status == ChunkStatus.COMPLETED))
            .count()
        )

        if total_chunks == completed_chunks:
            # All chunks processed, queue merging job
            job = session.query(Job).filter(Job.id == job_id).first()
            job.status = JobStatus.MERGING
            session.commit()

            redis_client.lpush("merge_queue", json.dumps({"job_id": job_id}))

    except Exception as e:
        chunk.status = ChunkStatus.FAILED
        chunk.error = str(e)
        session.commit()
        print(f"Error processing chunk {chunk_id}: {e}")


def check_for_unproccsed_chunks():
    session = Session()
    unprocessed_chunks = (
        session.query(Chunk).filter(Chunk.status == ChunkStatus.PENDING).all()
    )

    for chunk in unprocessed_chunks:
        # Check if the chunk is still pending
        if chunk.status == ChunkStatus.PENDING:
            # Requeue the chunk for processing if the queue is empty
            print(f"Requeuing chunk {chunk.id} for processing")
            redis_client.lpush(
                "process_queue",
                json.dumps(
                    {
                        "job_id": chunk.job_id,
                        "chunk_id": chunk.id,
                        "input_path": chunk.input_path,
                    }
                ),
            )
            print(f"Requeued chunk {chunk.id} for processing")

    # Close the session
    session.close()


def run_worker():
    print("Starting processor worker...")
    while True:
        # Block for 1 second, waiting for a job
        job_data = redis_client.brpop("process_queue", 1)

        if job_data:
            _, job_json = job_data
            job = json.loads(job_json)
            print(f"Processing chunk: {job['chunk_id']} for job: {job['job_id']}")
            process_chunk(job)

        else:
            if redis_client.llen("process_queue") == 0:
                check_for_unproccsed_chunks()

        # Small sleep to avoid busy-waiting
        time.sleep(0.1)


if __name__ == "__main__":
    run_worker()
