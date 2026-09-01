from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Capability(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    SFX = "sfx"
    MUSIC = "music"
    LIPSYNC = "lipsync"


@dataclass(frozen=True)
class GenerationJob:
    provider: str
    id: str
    capability: Capability


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    model: str
    capability: Capability
    display_name: str


@dataclass
class ImageGenerationRequest:
    prompt: str
    model: str
    num_candidates: int = 1
    reference_paths: list[str] = field(default_factory=list)
    output_path: str = ""


@dataclass
class ImageGenerationResult:
    artifact_path: str
    size_bytes: int


@dataclass
class ImageEditRequest:
    base_image_path: str
    instruction: str
    mask_path: str | None = None
    reference_paths: list[str] = field(default_factory=list)


@dataclass
class VideoGenerationRequest:
    prompt: str
    model: str
    reference_paths: list[str] = field(default_factory=list)
    duration_seconds: float = 5.0
    output_path: str = ""
    suppress_captions: bool = True


@dataclass
class VideoGenerationResult:
    artifact_path: str
    size_bytes: int
    duration_seconds: float


@dataclass
class LipsyncGenerationRequest:
    video_path: str
    audio_path: str
    model: str
    duration_seconds: float
    output_path: str = ""


@dataclass
class VoiceGenerationRequest:
    text: str
    model: str
    speaker: str = ""
    output_path: str = ""
    speaker_id: int | None = None
    voice_preset: str | None = None


@dataclass
class SfxGenerationRequest:
    prompt: str
    model: str
    video_path: str = ""
    output_path: str = ""


@dataclass
class MusicGenerationRequest:
    prompt: str
    model: str
    duration_seconds: float = 30.0
    output_path: str = ""


@dataclass
class AudioGenerationResult:
    artifact_path: str
    size_bytes: int
    duration_seconds: float
