# SilenceSwift Video Processing System

SilenceSwift is a distributed video processing system designed to automatically detect and speed up silent portions of videos based on [silencewarp](https://github.com/d1c0n/silencewarp). The system uses a microservices architecture to efficiently process videos through splitting, silence detection, speed adjustment, and merging.

## System Architecture Overview

```
Client -> API Server -> Redis Queues -> Workers -> S3 Storage
                            ^                         ^
                            |                         |
                            +-- Database <------------+
```

### Components Breakdown

#### API Server (Flask)

- Handles video uploads
- Manages job creation and tracking
- Provides status endpoints for clients
- Queues initial processing tasks

#### Redis Queues

- `split_queue`: Jobs waiting for video splitting
- `process_queue`: Chunks waiting for silence detection/speedup
- `merge_queue`: Completed chunks waiting to be merged

#### Worker Types

- **Splitter Workers**: Split videos into manageable chunks
- **Processor Workers**: Process individual chunks to detect and speed up silence
- **Merger Workers**: Merge processed chunks back into a complete video

#### Storage (MinIO)

- `input/`: Original uploaded videos
- `chunks/`: Split video chunks
- `processed/`: Chunks after silence processing
- `output/`: Final merged videos

#### Database (PostgreSQL)

- Tracks job status and metadata
- Maintains relationships between jobs and chunks
- Provides consistent state across all components

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Python 3.12 or higher (for local development)
- Poetry (for dependency management)

### Environment Setup

1. Clone the repository
2. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
3. Customize environment variables as needed

### Running with Docker Compose

```bash
# Start the entire system
docker-compose up

# To run in background
docker-compose up -d
```

This will start the PostgreSQL database, Redis, MinIO, and the API server.

### Manual Worker Startup

For development or customized deployments, you can run workers separately, dockerization to come:

```bash
# Start a splitter worker
python -m workers.splitter

# Start a processor worker
python -m workers.processor

# Start a merger worker
python -m workers.merger
```

## API Usage

### Submit a Video Job

```bash
curl -X POST -F "video=@your_video.mp4" http://localhost:5000/api/jobs
```

Response:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Job created successfully",
  "input_file": "your_video.mp4"
}
```

### Check Job Status

```bash
curl http://localhost:5000/api/jobs/550e8400-e29b-41d4-a716-446655440000
```

Response:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "created_at": "2023-07-01T12:34:56.789Z",
  "updated_at": "2023-07-01T12:35:00.123Z",
  "input_path": "input/550e8400-e29b-41d4-a716-446655440000/your_video.mp4",
  "output_path": null,
  "error": null,
  "chunks": { "pending": 2, "processing": 3, "completed": 5 },
  "progress": 50.0
}
```

## Processing Configuration

You can customize the silence detection and processing parameters through environment variables:

- `SILENCE_PERCENTILE`: Percentile used for calculating noise threshold (default: 30.0)
- `SILENCE_DURATION`: Minimum duration (in seconds) to consider a segment as silence (default: 0.35)
- `SPEED_FACTOR`: Factor by which silent segments are sped up (default: 10.0)

## Development

### Local Setup with Poetry

```bash
# Install dependencies
poetry install

# Activate virtual environment
eval (poetry env activate)

# Run API server locally
python -m api.app
```

### Database Schema

The system uses two main tables:

- `jobs`: Stores job metadata and overall status
- `chunks`: Stores information about individual video chunks

## License

This project is licensed under the MIT License - see the LICENSE file for details.
