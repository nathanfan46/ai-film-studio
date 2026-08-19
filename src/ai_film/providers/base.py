from __future__ import annotations

from typing import Protocol

from ai_film.models import (
    AudioGenerationResult,
    Capability,
    GenerationJob,
    ImageGenerationRequest,
    ImageGenerationResult,
    JobStatus,
    ModelInfo,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VideoGenerationRequest,
    VideoGenerationResult,
    VoiceGenerationRequest,
)


class ImageProvider(Protocol):
    def submit(self, request: ImageGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> ImageGenerationResult: ...


class VideoProvider(Protocol):
    def submit(self, request: VideoGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> VideoGenerationResult: ...


class AudioProvider(Protocol):
    def submit_voice(self, request: VoiceGenerationRequest) -> GenerationJob: ...
    def submit_sfx(self, request: SfxGenerationRequest) -> GenerationJob: ...
    def submit_music(self, request: MusicGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> AudioGenerationResult: ...


class ProviderCatalog(Protocol):
    def models(self, capability: Capability) -> list[ModelInfo]: ...
