"""
Video Processing API Service

This Flask application provides API endpoints for submitting video processing jobs
and retrieving their status.
"""

import json
import logging
import os
import sys
import uuid
from contextlib import contextmanager
from typing import Tuple, Iterator, List

from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, NotFound

from common.models import Base, Job, JobStatus
from common.storage import upload_file

import eventlet
from eventlet import wsgi

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()


class AppConfig:
    """Application configuration management"""

    def __init__(self) -> None:
        """Initialize application configuration from environment variables"""
        # Database configuration
        self.db_url = os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://user:password@localhost/video_processor",
        )

        # Redis configuration
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.redis_timeout = int(os.environ.get("REDIS_TIMEOUT", "5"))  # seconds

        # File upload configuration
        self.max_content_length = int(
            os.environ.get("MAX_CONTENT_LENGTH", 1024 * 1024 * 2000)
        )  # 2GB default
        self.allowed_extensions = os.environ.get(
            "ALLOWED_EXTENSIONS", "mp4,avi,mov,mkv"
        ).split(",")

        # Paths configuration
        self.input_folder = os.environ.get("INPUT_FOLDER", "input")

        # Queue configuration
        self.split_queue = os.environ.get("SPLIT_QUEUE", "split_queue")

        # Server configuration
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "5000"))
        self.debug = os.environ.get("DEBUG", "false").lower() == "true"

    def validate(self) -> List[str]:
        """
        Validate the configuration, returning a list of any errors.

        Returns:
            List[str]: A list of error messages, or an empty list if valid
        """
        errors = []

        # Validate database URL
        if not self.db_url or "://" not in self.db_url:
            errors.append(f"Invalid DATABASE_URL: {self.db_url}")

        # Validate Redis URL
        if not self.redis_url or "://" not in self.redis_url:
            errors.append(f"Invalid REDIS_URL: {self.redis_url}")

        # Validate content length
        if self.max_content_length <= 0:
            errors.append(f"Invalid MAX_CONTENT_LENGTH: {self.max_content_length}")

        # Validate allowed extensions
        if not self.allowed_extensions:
            errors.append("No allowed file extensions specified")

        # Validate port
        if not (1 <= self.port <= 65535):
            errors.append(f"Invalid port number: {self.port}")

        return errors


def create_app() -> Flask:
    """
    Factory function to create and configure the Flask application.

    Returns:
        Flask: The configured Flask application
    """
    app = Flask(__name__)

    # Load application configuration
    config = AppConfig()

    # Validate configuration
    config_errors = config.validate()
    if config_errors:
        for error in config_errors:
            logger.critical(f"Configuration error: {error}")
        sys.exit(1)

    app.config["MAX_CONTENT_LENGTH"] = config.max_content_length

    # Initialize database connection
    try:
        engine = create_engine(config.db_url)
        # Test the connection
        with engine.connect() as _:
            pass

        Base.metadata.create_all(engine)
        DbSession = sessionmaker(bind=engine)
        logger.info("Database connection established successfully")
    except SQLAlchemyError as e:
        logger.critical(f"Failed to connect to database: {str(e)}")
        sys.exit(1)

    # Initialize Redis connection
    try:
        redis_client = redis.from_url(
            config.redis_url,
            socket_timeout=config.redis_timeout,
            socket_connect_timeout=config.redis_timeout,
        )
        # Test the connection
        redis_client.ping()
        logger.info("Redis connection established successfully")
    except redis.RedisError as e:
        logger.critical(f"Failed to connect to Redis: {str(e)}")
        sys.exit(1)

    @contextmanager
    def db_session() -> Iterator[Session]:
        """
        Context manager for database sessions to ensure proper cleanup.

        Yields:
            Session: A SQLAlchemy session

        Raises:
            SQLAlchemyError: If a database error occurs
        """
        session = DbSession()
        try:
            yield session
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error, rolling back transaction: {str(e)}")
            raise
        finally:
            session.close()

    def allowed_file(filename: str) -> bool:
        """
        Check if the file extension is allowed.

        Args:
            filename: The name of the file to check

        Returns:
            bool: True if file extension is allowed, False otherwise
        """
        if not filename or "." not in filename:
            return False

        extension = filename.rsplit(".", 1)[1].lower()
        return extension in config.allowed_extensions

    @app.route("/api/jobs", methods=["POST"])
    def create_job() -> Tuple[Response, int]:
        """
        API endpoint to create a new video processing job.

        Expects a video file in the request.

        Returns:
            Response: JSON response with job details and HTTP status code
        """
        try:
            # Check if video file is in the request
            if "video" not in request.files:
                logger.warning("API request missing video file")
                return jsonify({"error": "No video file provided"}), 400

            video_file = request.files["video"]

            # Check if the file has a name (not empty submission)
            if video_file.filename == "":
                logger.warning("Empty filename submitted")
                return jsonify({"error": "Empty filename submitted"}), 400

            # Validate file type
            if not allowed_file(video_file.filename):
                logger.warning(f"File type not allowed: {video_file.filename}")
                return jsonify(
                    {
                        "error": f"File type not allowed. Allowed types: {', '.join(config.allowed_extensions)}"
                    }
                ), 400

            # Generate unique job ID
            job_id = str(uuid.uuid4())
            logger.info(f"Creating new job with ID: {job_id}")

            # Create directory structure if it doesn't exist
            job_directory = f"{config.input_folder}/{job_id}"

            # Save original file to storage
            input_path = f"{job_directory}/{video_file.filename}"

            try:
                upload_file(video_file, input_path)
                logger.info(f"Video file uploaded to {input_path}")
            except Exception as e:
                logger.error(f"Failed to upload file: {str(e)}")
                return jsonify({"error": f"Failed to upload file: {str(e)}"}), 500

            # Create job in database
            try:
                with db_session() as session:
                    job = Job(
                        id=job_id, input_path=input_path, status=JobStatus.PENDING
                    )
                    session.add(job)
                    session.commit()
                    logger.info(f"Job {job_id} created in database")
            except SQLAlchemyError as e:
                logger.error(f"Database error creating job: {str(e)}")
                return jsonify({"error": "Failed to create job in database"}), 500

            # Queue the splitting task
            try:
                job_data = {"job_id": job_id, "input_path": input_path}

                redis_client.lpush(config.split_queue, json.dumps(job_data))
                logger.info(f"Job {job_id} queued for processing")
            except (redis.RedisError, json.JSONDecodeError) as e:
                logger.error(f"Redis error queuing job: {str(e)}")

                # Update job status to error in case of Redis failure
                try:
                    with db_session() as session:
                        job = session.query(Job).filter(Job.id == job_id).first()
                        if job:
                            job.status = JobStatus.ERROR
                            job.error = f"Failed to queue job: {str(e)}"
                            session.commit()
                            logger.info(f"Updated job {job_id} status to ERROR")
                except SQLAlchemyError as db_err:
                    logger.error(f"Failed to update job status to error: {str(db_err)}")

                return jsonify({"error": "Failed to queue job for processing"}), 500

            # Prepare successful response
            response_data = {
                "job_id": job_id,
                "status": JobStatus.PENDING.value,
                "message": "Job created successfully",
                "input_file": video_file.filename,
            }

            logger.info(f"Job {job_id} created successfully")
            return jsonify(response_data), 201

        except Exception as e:
            logger.exception(f"Unexpected error in create_job: {str(e)}")
            return jsonify({"error": "An unexpected error occurred"}), 500

    @app.route("/api/jobs/<job_id>", methods=["GET"])
    def get_job(job_id: str) -> Tuple[Response, int]:
        """
        API endpoint to retrieve job status and details.

        Args:
            job_id: UUID of the job to retrieve

        Returns:
            Response: JSON response with job details and HTTP status code
        """
        try:
            # Validate job_id format
            try:
                uuid_obj = uuid.UUID(job_id)
                # Ensure job_id string matches the canonical representation
                if str(uuid_obj) != job_id:
                    logger.warning(f"Invalid job_id format: {job_id}")
                    return jsonify({"error": "Invalid job ID format"}), 400
            except ValueError:
                logger.warning(f"Invalid job_id format: {job_id}")
                return jsonify({"error": "Invalid job ID format"}), 400

            logger.info(f"Retrieving job details for job_id: {job_id}")

            try:
                with db_session() as session:
                    # Query the job with all its chunks
                    job = session.query(Job).filter(Job.id == job_id).first()

                    if not job:
                        logger.warning(f"Job not found: {job_id}")
                        return jsonify({"error": "Job not found"}), 404

                    # Count chunks by status
                    chunks_stats = {}
                    for chunk in job.chunks:
                        status = chunk.status.value
                        chunks_stats[status] = chunks_stats.get(status, 0) + 1

                    # Calculate progress if applicable
                    progress = None
                    if job.status == JobStatus.PROCESSING and job.chunks:
                        # Calculate progress based on completed chunks
                        total_chunks = len(job.chunks)
                        completed_chunks = sum(
                            1
                            for chunk in job.chunks
                            if chunk.status == JobStatus.COMPLETED
                        )
                        progress = (
                            round((completed_chunks / total_chunks) * 100, 2)
                            if total_chunks > 0
                            else 0
                        )

                    # Build response data
                    response_data = {
                        "job_id": job.id,
                        "status": job.status.value,
                        "created_at": job.created_at.isoformat(),
                        "updated_at": job.updated_at.isoformat(),
                        "input_path": job.input_path,
                        "output_path": job.output_path,
                        "error": job.error,
                        "chunks": chunks_stats,
                    }

                    # Add progress information if available
                    if progress is not None:
                        response_data["progress"] = progress

                    logger.info(f"Job {job_id} details retrieved successfully")
                    return jsonify(response_data), 200

            except SQLAlchemyError as e:
                logger.error(f"Database error retrieving job: {str(e)}")
                return jsonify({"error": "Failed to retrieve job details"}), 500

        except Exception as e:
            logger.exception(f"Unexpected error in get_job: {str(e)}")
            return jsonify({"error": "An unexpected error occurred"}), 500

    # Error handlers
    @app.errorhandler(404)
    def not_found(e: NotFound) -> Tuple[Response, int]:
        """Handle 404 errors"""
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(400)
    def bad_request(e: BadRequest) -> Tuple[Response, int]:
        """Handle 400 errors"""
        return jsonify({"error": str(e)}), 400

    @app.errorhandler(413)
    def request_entity_too_large(e: Exception) -> Tuple[Response, int]:
        """Handle 413 errors (file too large)"""
        return jsonify(
            {
                "error": "File too large",
                "max_size_mb": config.max_content_length / (1024 * 1024),
            }
        ), 413

    @app.errorhandler(500)
    def internal_server_error(e: Exception) -> Tuple[Response, int]:
        """Handle 500 errors"""
        logger.exception("Internal server error")
        return jsonify({"error": "Internal server error"}), 500

    # Health check endpoint
    @app.route("/health", methods=["GET"])
    def health_check() -> Tuple[Response, int]:
        """
        Health check endpoint to verify service is running.

        Returns:
            Response: JSON response with service status
        """
        status = {"status": "healthy", "service": "video-processor-api"}

        # Check database connectivity
        try:
            with db_session() as session:
                # Simple query to test database connectivity
                session.query(Job).limit(1).all()
            status["database"] = "connected"
        except SQLAlchemyError as e:
            status["database"] = "error"
            status["database_error"] = str(e)
            status["status"] = "degraded"

        # Check Redis connectivity
        try:
            redis_client.ping()
            status["redis"] = "connected"
        except redis.RedisError as e:
            status["redis"] = "error"
            status["redis_error"] = str(e)
            status["status"] = "degraded"

        http_status = 200 if status["status"] == "healthy" else 503
        return jsonify(status), http_status

    return app


if __name__ == "__main__":
    # Create and run the Flask app
    app = create_app()
    config = AppConfig()

    logger.info(f"Starting Video Processing API on {config.host}:{config.port}")
    # app.run(host=config.host, port=config.port, debug=config.debug)
    wsgi.server(eventlet.listen((config.host, config.port)), app, log_output=sys.stdout)
