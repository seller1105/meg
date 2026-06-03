# Meg — Vision Document

> *An AI-powered FFmpeg assistant for the terminal*

-----

## What Is Meg?

Meg is an open-source, AI-powered command-line tool that lets video engineers and developers construct, understand, and debug FFmpeg commands using plain English. Instead of memorizing flags, hunting Stack Overflow, or deciphering cryptic error output, you describe what you want — Meg gives you the command, explains it, and gets out of your way.

-----

## The Problem

FFmpeg is one of the most powerful and widely used tools in existence. It is also notoriously hostile to learn and frustrating to use at any level. Even experienced users spend time:

- Reconstructing complex filter chains from memory or old scripts
- Debugging opaque error messages with no clear explanation
- Hunting for the exact flag combination for a specific delivery format
- Manually testing encode settings to find the right tradeoff

No well-known AI-native tool exists to solve this. Meg fills that gap.

-----

## Core Philosophy

- **CLI-first, always.** Meg lives in the terminal. No GUI, no Electron app, no browser dependency.
- **Smart, not magic.** Meg explains what it’s doing and why. Users should understand the output, not just copy-paste it.
- **Respect the user’s expertise.** Meg is built for people who know what they’re doing. It augments power users, not hand-holds beginners.
- **Open source, community-driven.** The core tool is free forever. The community makes it better.

-----

## Target Audience

**Primary (v0.1):**

- Post-production professionals — engineers, operators, QC leads working with broadcast, streaming, and cinema formats
- Developers and DevOps engineers working with media pipelines, transcoding, and video automation

These two audiences share a common trait: they already know FFmpeg. Meg makes them faster, not dependent.

-----

## v0.1 Scope — The Only Three Things That Matter

The first release does exactly this, and nothing more:

1. **Accept a plain-English request** describing a video operation
1. **Output a correct, ready-to-run FFmpeg command**
1. **Briefly explain what the command does and why**

That loop must work reliably and feel polished. No feature is worth adding until that core experience is excellent.

-----

## AI Backend

Meg supports two AI providers from day one, with user choice:

- **Anthropic (Claude)** — via `ANTHROPIC_API_KEY`
- **OpenAI (GPT)** — via `OPENAI_API_KEY`

Provider is configurable via environment variable or a `--provider` flag. Meg will default to whichever key is detected, with Claude preferred if both are present.

Users supply their own API keys. Meg does not proxy or manage API credentials.

-----

## CLI Interface

```bash
# Basic usage
meg "convert this mkv to h264 mp4 with aac audio"

# Explicit provider
meg --provider openai "extract audio as 24bit wav"

# Explain an existing command
meg --explain "ffmpeg -i input.mp4 -vf scale=1920:1080 -c:v libx264 output.mp4"

# Verbose mode — more detailed explanation
meg --verbose "package this as an IMF deliverable"
```

-----

## What Meg Is NOT (v0.1)

- Not a GUI tool
- Not a batch processor
- Not a workflow automation engine
- Not a preset library
- Not a replacement for knowing FFmpeg

These may come later. They are not v0.1.

-----

## Roadmap Candidates (Post v0.1)

In rough priority order, based on power-user value:

1. **Error Interpreter** — intercept FFmpeg stderr, explain failures in plain English
1. **Command Explainer** — paste any FFmpeg command, get a full breakdown
1. **Workflow Memory** — remember user preferences and common settings per project
1. **Filtergraph Visualizer** — render complex filter chains as a readable DAG
1. **Codec Decision Engine** — recommend codec/encoding strategy based on source, target, and constraints
1. **Hardware Acceleration Awareness** — detect NVENC, VideoToolbox, QSV and suggest hardware paths
1. **Preset Library** — community-contributed delivery spec presets (Netflix, Apple, Dolby, broadcast)
1. **Validation Layer** — pre-flight check commands against known delivery specs
1. **Batch Intelligence** — generate shell scripts for folder-level operations
1. **Benchmark Mode** — compare encode configurations across speed, size, and quality

-----

## Technical Stack (v0.1)

- **Language:** Python 3.11+
- **CLI Framework:** Typer
- **AI SDKs:** `anthropic`, `openai`
- **Packaging:** pip installable via PyPI (`pip install meg-cli`)
- **Config:** Environment variables + optional `~/.meg/config.toml`

-----

## License

MIT — maximum adoption, minimum friction. The community and ecosystem matter more than legal protection at this stage.

-----

## Success Criteria for v0.1

- A post-production engineer or developer can install Meg in under 2 minutes
- The first command they run produces a correct, usable FFmpeg command
- They understand what the command does without having to look anything up
- They tell someone else about it

-----

## North Star

> Meg is the FFmpeg expert you wish you had next to you in the terminal.

-----

*Document version: 0.1 — Created May 2026*