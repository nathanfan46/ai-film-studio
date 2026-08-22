# ai-film

`ai-film` is a standalone, provider-independent CLI and engine for an AI film-production
pipeline. It scaffolds a project directory, tracks each shot's generation lifecycle
(image, video, voice, sfx, music) through a JSON shot store with a cost-gated approval
workflow, and renders the completed shots into a final video with ffmpeg.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Scaffold a new project directory tree and default config.json
ai-film init "My Film"

# List the available provider/model catalog for a capability
ai-film models --capability image

# Approve a scope of shots for generation before spending is allowed
ai-film approve-generation --scope storyboard --targets S01_SH01,S01_SH02

# Generate an image and a video for a shot
ai-film generate-image --shot S01_SH01
ai-film generate-video --shot S01_SH01

# Check overall project status and render the final video
ai-film status
ai-film render
```

Run `ai-film --help` (or `ai-film <command> --help`) for the full command reference.

## Configuration

The fal.ai backend requires a `FAL_KEY` environment variable to be set:

```bash
export FAL_KEY="your-fal-api-key"
```
