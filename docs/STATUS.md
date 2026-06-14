# Meg — Project status (handoff)

> **Read this first** in a new chat session. Product intent: [VISION.md](../VISION.md). Milestones: [v0.1-roadmap.md](v0.1-roadmap.md).

**Last updated:** June 2026  
**Version:** `0.2.0` (source-aware generate via ffprobe; PyPI upload pending)

---

## What Meg is

Open-source CLI: plain English → FFmpeg command + explanation, or `--explain` on an existing command. Python 3.11+, Typer, Anthropic + OpenAI (user API keys). When a prompt references a real local media file, Meg auto-runs `ffprobe` and injects compact source metadata into the model context.

---

## Current state (summary)

| Phase | Status | Notes |
|-------|--------|--------|
| 0 Bootstrap | Done | Package, Typer, pytest, `.venv` workflow |
| 1 Config & providers | Done | `config.py`, `providers/*`, `--provider`, key auto-detect (Claude preferred) |
| 2 Generate loop | Done | `build_generate_prompt`, `parse_generate_response`, wired in `cli.py` |
| 3 Explain mode | Done | `build_explain_prompt`, `parse_explain_response`, `--explain` wired |
| 4 Prompt hardening | Done | Guardrails in `SYSTEM_PROMPT_GENERATE`; G4/G11/G14 pass content review |
| 5 CLI polish | Done | UTF-8 stdout, actionable API errors, 60s provider timeouts |
| 6 Ship | Ready | MIT `LICENSE`, README; GitHub `origin` on `main`; wheel/sdist verified; PyPI + tag pending |
| 7 Source-aware generate | Done | `meg/ffprobe.py` — path detect, guarded probe, prompt injection, minimal-change rules |

**Tests:** `pytest` — **57 passed**, 1 skipped (mocked providers + ffprobe unit tests; no live API in CI).

---

## v0.2.0 — Source-aware generate (ffprobe)

When a generate or explain prompt contains a real local media path:

1. **Detect** — regex extracts quoted, Windows, or bare paths with known media extensions.
2. **Guard** — `can_probe()` requires: file exists, readable, not UNC, ≤ 50 GiB.
3. **Probe** — `ffprobe` via argv array (no shell), 30s timeout; JSON summarized (codec, container, resolution, fps, pixel format, color, audio layout, duration).
4. **Inject** — compact summary + default output path (`<stem>_out<ext>`) added to the user prompt.
5. **Generate** — system prompt enforces: never overwrite input; preserve probed specs except what the user asked to change; cite probed facts in the explanation.

If ffprobe is missing, the path does not exist, or guards fail — Meg falls back to generic generate (no error).

---

## How to run locally

```powershell
cd d:\projects\otrm\meg
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# If key was set via setx, refresh in this session:
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")

meg "convert mkv to h264 mp4"
meg "convert `"D:\path\to\clip.mov`" to UHD 23.98 fps"
meg --explain "ffmpeg -i input.mp4 -vf scale=1920:1080 -c:v libx264 output.mp4"
pytest
```

**API keys (never commit):** `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`. Optional: `MEG_PROVIDER=anthropic|openai`, optional `~/.meg/config.toml`. See [.env.example](../.env.example).

---

## Key files

| File | Role |
|------|------|
| `meg/cli.py` | Entry point; generate + explain flows; wires ffprobe before prompt build |
| `meg/ffprobe.py` | Path extraction, `can_probe()`, ffprobe subprocess, JSON summary, default output path |
| `meg/prompt.py` | System prompts, builders, parsers (`COMMAND:` / `EXPLANATION:` schema) |
| `meg/config.py` | Env + TOML config, provider resolution |
| `meg/providers/` | `AIProvider`, Anthropic, OpenAI, `create_provider()` |
| `scripts/run_qa_suite.py` | Runs G1–G15 + E1–E8 against live API → `docs/qa-run.json` |
| `scripts/summarize_qa.py` | Prints pass/fail + rubric keyword checks from `qa-run.json` |

---

## QA results (latest full suite)

Captured in [qa-run.json](qa-run.json) (live API run after Phase 4 prompt hardening). Path-based / ffprobe-aware prompts not yet in the G1–G15 suite — add manual QA for real-file workflows.

### Generate G1–G15

- **15/15** exited 0; content review pass (generic prompts, no local paths).
- **G4** — MP4 output, single-pass `loudnorm` with two-pass note in explanation.
- **G11** — ProRes/MXF master with explicit IMF placeholder disclaimer (no CPL/ASSETMAP).
- **G14** — Explicit `-map 0:v:0 -map 0:a:0` indices; ffprobe verification note.

### Explain E1–E8

- **8/8** pass; content quality good.

Re-run: `python scripts/run_qa_suite.py` then `python scripts/summarize_qa.py`.

---

## Next priorities (recommended order)

1. **Publish:** `python -m build` then `twine upload dist/*`; tag `v0.2.0` and `git push origin v0.2.0`
2. **Verify PyPI:** fresh venv → `pip install meg-cli` → `meg --help` → `pip check`
3. **QA:** add path-based prompts to manual suite (real `.mov` / `.mkv` on disk)
4. **Optional:** `.env` loading (currently env vars only; `.env` is gitignored but not auto-loaded)

---

## Out of scope for v0.1 / v0.2

Error interpreter, batch, presets, GUI, hardware detection — see roadmap and `.cursor/rules`.

---

## Definition of done (quick check)

- [x] Generate + explain implemented
- [x] Providers + config
- [x] pytest green (57 passed)
- [x] Prompt suite consistently "power-user good" (Phase 4)
- [x] Package builds; wheel/sdist install in clean venv
- [x] Source-aware generate via ffprobe (v0.2.0)
- [ ] Ship to PyPI + release tag `v0.2.0`
