from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import datetime
import enum

Base = declarative_base()


class JobStatus(enum.Enum):
    PENDING = "pending"
    SPLITTING = "splitting"
    PROCESSING = "processing"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


class ChunkStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    input_path = Column(String, nullable=False)
    output_path = Column(String)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    error = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    chunks = relationship("Chunk", back_populates="job")


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("jobs.id"))
    chunk_number = Column(Integer)
    input_path = Column(String, nullable=False)
    output_path = Column(String)
    status = Column(Enum(ChunkStatus), default=ChunkStatus.PENDING)
    error = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    job = relationship("Job", back_populates="chunks")
