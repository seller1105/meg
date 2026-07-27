# Meg

AI-powered FFmpeg assistant for the terminal. Describe what you want in plain English — Meg returns a ready-to-run `ffmpeg` command and a short explanation. Paste an existing command with `--explain` to get a breakdown.

**Status:** v0.2.0 — source-aware generate via auto ffprobe; PyPI upload pending. See [docs/STATUS.md](docs/STATUS.md).

## Docs

| Doc | Purpose |
|-----|---------|
| [VISION.md](VISION.md) | Product goals and philosophy |
| [docs/v0.1-roadmap.md](docs/v0.1-roadmap.md) | Milestones, test prompt suite (G1–G15, E1–E8) |
| [docs/STATUS.md](docs/STATUS.md) | **Current progress and next steps** (start here in a new session) |

## Requirements

- Python 3.11+
- **ffprobe** on `PATH` (optional but recommended — Meg auto-probes local media files referenced in prompts)
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

### From PyPI

Not on PyPI yet. After `twine upload`, install with:

```bash
pip install meg-cli
meg --help
```

Pre-publish check (clean venv): `python -m build` then `pip install dist/meg_cli-*.whl`.

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

# Path-based request — Meg ffprobes the file and tailors the command to real source specs
meg "convert `"D:\renders\master.mov`" to UHD 23.98 fps"

# Interactive session (REPL) — just run meg with no arguments
meg

# Preset vault — save a command once, reuse it on any file later
meg preset save proxy-1080 "ffmpeg -i input.mov -vf scale=-2:1080 -c:v libx264 -crf 20 -c:a aac out.mp4" -d "1080p editorial proxy"
meg preset run proxy-1080 "D:\renders\new shot.mov"
meg preset list
meg preset search proxy
meg preset delete proxy-1080
```

**Default output (generate):** one `ffmpeg` line, blank line, then a short bullet explanation.

**Explain mode:** prints only the breakdown (no echoed command).

**Path-based generate:** when your prompt includes a real local media path (not a placeholder like `input.mkv`), Meg runs `ffprobe`, injects a compact summary into the model context, and expects commands that:

- Use the probed file as `-i`
- Default output to `<stem>_out<ext>` beside the source (never overwrite the input)
- Change only what you asked for; preserve probed codec, pixel format, color, and audio specs otherwise

Probing is skipped for missing paths, network (UNC) paths, unreadable files, files over 50 GiB, or if ffprobe is not installed. ffprobe runs via argv (no shell) with a 30s timeout. **Probe results are cached** per file (path + mtime + size) for the lifetime of the process, so edit/revise turns and repeat lookups do not re-run ffprobe.

**Interactive session (REPL):** bare `meg` on a terminal opens a conversational session. Type requests in plain English, refine with edit feedback, `explain <command>`, and `save <nickname>` the last generated command as a preset. `help` lists all session commands.

**Preset vault:** `meg preset save/list/search/run/delete` manages reusable commands in `~/.meg/presets.toml`. Input and output paths are templatized on save, so `meg preset run <nickname> <file>` applies the same encode settings to any source file — the output path is re-derived beside the new input (extension preserved from the preset). Presets can also be saved from the post-generate menu (`[s]ave`) or from the REPL. Preset runs go through the same pre-flight checks and per-command approval as generated commands.

**Run generated commands (interactive):** after generate, Meg offers `[r]un  [e]dit  [s]ave preset  [q]uit`:

- **Per-command approval** — choosing run shows the full command and asks `[y]es / [n]o`; each revised command requires fresh approval
- **Safety checks** — refuses input=output paths; warns and confirms before overwriting an existing output file; strips model-supplied `-y` (Meg adds it only after you confirm overwrite)
- **No-shell execution** — argv array only; `ffmpeg` / `ffprobe` allowlist; clear errors when binaries are missing
- **Long encodes** — live progress bar on a TTY (percent + ETA when source duration is known, otherwise time/speed); press `q` to cancel or Ctrl+C to interrupt; **stall timeout** (default 180s without stderr activity, not a max encode length) via `MEG_EXEC_STALL_TIMEOUT_S`

**Error Interpreter:** when a run fails, Meg prints the failure summary and offers `[f]ix with AI  [e]dit  [q]uit`. Choosing fix sends the failed command and stderr excerpt to the model, prints a plain-English diagnosis, and drops the corrected command back into the normal approval loop (fresh approval + pre-flight). If no command change can fix it (missing file, missing encoder), Meg says so instead of inventing a command. No API call happens unless you choose fix.

**Terminal UI:** on an interactive terminal Meg renders with [Rich](https://github.com/Textualize/rich) — a spinner during AI calls, generated commands in a syntax-highlighted panel, a live encode progress bar, and `preset list`/`search` as a table. Piped or redirected output stays plain paste-able text (no ANSI, no boxes), and `NO_COLOR` is respected.

**`--verbose`:** asks the model for a deeper explanation in both generate and explain modes. Default output stays minimal.

**Models:** defaults are `claude-sonnet-4-5` (Anthropic) and `gpt-5` (OpenAI). Override per provider via `MEG_ANTHROPIC_MODEL` / `MEG_OPENAI_MODEL`, `~/.meg/config.toml` (`anthropic_model`, `openai_model`), or `--model` for the active provider.

**Environment (optional):** `MEG_EXEC_STALL_TIMEOUT_S` — seconds without ffmpeg stderr before Meg treats an encode as hung (default `180`). See [.env.example](.env.example).

**.env auto-loading:** Meg loads `.env` from the current directory, then `~/.meg/.env` (first match wins per variable). Real environment variables always take precedence, and `.env` stays gitignored — convenient for keys without `setx`. No dependency; simple `KEY=VALUE` lines with `#` comments, optional `export`, and quoted values.

## Examples

| Task | Command |
|------|---------|
| Transcode to H.264/AAC | `meg "convert mkv to h264 mp4 with aac"` |
| Broadcast loudness | `meg --verbose "normalize loudness to -23 LUFS"` |
| Remux without re-encode | `meg "remux mkv to mp4, copy all streams"` |
| Real file on disk | `meg "scale `"D:\clips\shot.mov`" to 1920x1080"` |
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
git tag v0.2.0
git push origin v0.2.0
```

## Project layout

```
meg/
├── meg/           # package (cli, config, prompt, ffprobe, exec, providers)
├── tests/
├── docs/          # roadmap, STATUS, qa-run.json
├── scripts/       # QA helpers
├── VISION.md
└── pyproject.toml
```

## License

MIT — see [LICENSE](LICENSE).
