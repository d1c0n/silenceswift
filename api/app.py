# api/app.py
import os
import uuid
from flask import Flask, request, jsonify
import redis
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from common.models import Base, Job, JobStatus
from common.storage import upload_file

app = Flask(__name__)

# Initialize database connection
DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://user:password@localhost/video_processor"
)
engine = create_engine(DB_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Initialize Redis connection
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL)


@app.route("/api/jobs", methods=["POST"])
def create_job():
    if "video" not in request.files:
        return jsonify({"error": "No video file provided"}), 400

    video_file = request.files["video"]

    # Generate unique job ID
    job_id = str(uuid.uuid4())

    # Save original file to storage
    input_path = f"input/{job_id}/{video_file.filename}"
    upload_file(video_file, input_path)

    # Create job in database
    session = Session()
    job = Job(id=job_id, input_path=input_path, status=JobStatus.PENDING)
    session.add(job)
    session.commit()

    # Queue the splitting task
    redis_client.lpush(
        "split_queue", json.dumps({"job_id": job_id, "input_path": input_path})
    )

    return jsonify({"job_id": job_id, "status": "pending"}), 201


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    session = Session()
    job = session.query(Job).filter(Job.id == job_id).first()

    if not job:
        return jsonify({"error": "Job not found"}), 404

    # Count chunks by status
    chunks_stats = {}
    for chunk in job.chunks:
        status = chunk.status.value
        chunks_stats[status] = chunks_stats.get(status, 0) + 1

    return jsonify(
        {
            "job_id": job.id,
            "status": job.status.value,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "input_path": job.input_path,
            "output_path": job.output_path,
            "error": job.error,
            "chunks": chunks_stats,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
