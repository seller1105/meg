# Meg

AI-powered FFmpeg assistant for the terminal. Describe what you want in plain English — Meg returns a ready-to-run `ffmpeg` command and a short explanation. Paste an existing command with `--explain` to get a breakdown.

**Status:** v0.1 release-ready. See [docs/STATUS.md](docs/STATUS.md) for the latest handoff.

## Docs

| Doc | Purpose |
|-----|---------|
| [VISION.md](VISION.md) | Product goals and philosophy |
| [docs/v0.1-roadmap.md](docs/v0.1-roadmap.md) | Milestones, test prompt suite (G1–G15, E1–E8) |
| [docs/STATUS.md](docs/STATUS.md) | **Current progress and next steps** (start here in a new session) |

## Requirements

- Python 3.11+
- An API key: [Anthropic](https://console.anthropic.com/) (`ANTHROPIC_API_KEY`) and/or [OpenAI](https://platform.openai.com/) (`OPENAI_API_KEY`)

Meg does not ship or proxy credentials. See [.env.example](.env.example) for variable names (do not commit real keys).

### API keys on Windows

Persistent (user scope), then **open a new terminal**:

```cmd
setx ANTHROPIC_API_KEY "your_key_here"
```

In PowerShell, verify with:

```powershell
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
```

If you use Command Prompt, use `echo %ANTHROPIC_API_KEY%` (not `$env:...`).

Optional config file: `~/.meg/config.toml` (keys and default provider). Environment variables override the file.

## Install

### From PyPI (when published)

```bash
pip install meg-cli
meg --help
```

### Development

Use a virtual environment:

#### Windows (PowerShell)

```powershell
cd path\to\meg
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
meg --help
pytest
```

If activation fails:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

#### macOS / Linux

```bash
cd path/to/meg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
meg --help
pytest
```

## Usage

```bash
# Generate a command from plain English
meg "convert this mkv to h264 mp4 with aac audio"

# Longer explanation (more detail on codecs, filters, mapping)
meg --verbose "normalize loudness to -23 LUFS for broadcast"

# Explain an existing command
meg --explain "ffmpeg -i input.mp4 -vf scale=1920:1080 -c:v libx264 output.mp4"

# Force provider (optional; Claude is preferred when both keys are set)
meg --provider openai "extract audio as 24-bit wav"

# Override model for the selected provider
meg --model claude-sonnet-4-5 "convert mkv to h264 mp4"
```

**Default output (generate):** one `ffmpeg` line, blank line, then a short bullet explanation.

**Explain mode:** prints only the breakdown (no echoed command).

**`--verbose`:** asks the model for a deeper explanation in both generate and explain modes. Default output stays minimal.

**Models:** defaults are `claude-sonnet-4-5` (Anthropic) and `gpt-5` (OpenAI). Override per provider via `MEG_ANTHROPIC_MODEL` / `MEG_OPENAI_MODEL`, `~/.meg/config.toml` (`anthropic_model`, `openai_model`), or `--model` for the active provider.

## Examples

| Task | Command |
|------|---------|
| Transcode to H.264/AAC | `meg "convert mkv to h264 mp4 with aac"` |
| Broadcast loudness | `meg --verbose "normalize loudness to -23 LUFS"` |
| Remux without re-encode | `meg "remux mkv to mp4, copy all streams"` |
| Explain scaling | `meg --explain "ffmpeg -i in.mp4 -vf scale=1920:1080 -c:v libx264 out.mp4"` |

## QA scripts

Run the roadmap test suite against a live API (writes `docs/qa-run.json`):

```powershell
python scripts/run_qa_suite.py
python scripts/summarize_qa.py
```

## Release (maintainers)

```bash
python -m pip install build twine
python -m build
twine upload dist/*
git tag v0.1.0
git push origin v0.1.0
```

## Project layout

```
meg/
├── meg/           # package (cli, config, prompt, providers)
├── tests/
├── docs/          # roadmap, STATUS, qa-run.json
├── scripts/       # QA helpers
├── VISION.md
└── pyproject.toml
```

## License

MIT — see [LICENSE](LICENSE).
