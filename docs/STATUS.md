# Meg — Project status (handoff)



> **Read this first** in a new chat session. Product intent: [VISION.md](../VISION.md). Milestones: [v0.1-roadmap.md](v0.1-roadmap.md).



**Last updated:** May 2026  

**Version:** `0.1.0` (release-ready; PyPI publish pending)



---



## What Meg is



Open-source CLI: plain English → FFmpeg command + explanation, or `--explain` on an existing command. Python 3.11+, Typer, Anthropic + OpenAI (user API keys).



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

| 6 Ship | Ready | MIT `LICENSE`, README; git + CI on `main` locally; GitHub push needs `gh auth` |



**Tests:** `pytest` — **29 passed** (mocked providers; no live API in CI).



---



## How to run locally



```powershell

cd d:\projects\otrm\meg

.\.venv\Scripts\Activate.ps1

python -m pip install -e ".[dev]"



# If key was set via setx, refresh in this session:

$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")



meg "convert mkv to h264 mp4"

meg --explain "ffmpeg -i input.mp4 -vf scale=1920:1080 -c:v libx264 output.mp4"

pytest

```



**API keys (never commit):** `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`. Optional: `MEG_PROVIDER=anthropic|openai`, optional `~/.meg/config.toml`. See [.env.example](../.env.example).



---



## Key files



| File | Role |

|------|------|

| `meg/cli.py` | Entry point; generate + explain flows; UTF-8 `_echo()`; provider error formatting |

| `meg/prompt.py` | System prompts, builders, parsers (`COMMAND:` / `EXPLANATION:` schema) |

| `meg/config.py` | Env + TOML config, provider resolution |

| `meg/providers/` | `AIProvider`, Anthropic, OpenAI, `create_provider()` |

| `scripts/run_qa_suite.py` | Runs G1–G15 + E1–E8 against live API → `docs/qa-run.json` |

| `scripts/summarize_qa.py` | Prints pass/fail + rubric keyword checks from `qa-run.json` |



---



## QA results (latest full suite)



Captured in [qa-run.json](qa-run.json) (live API run after Phase 4 prompt hardening).



### Generate G1–G15



- **15/15** exited 0; all pass content review.

- **G4** — MP4 output, single-pass `loudnorm` with two-pass note in explanation.

- **G11** — ProRes/MXF master with explicit IMF placeholder disclaimer (no CPL/ASSETMAP).

- **G14** — Explicit `-map 0:v:0 -map 0:a:0` indices; `ffprobe` verification note.



### Explain E1–E8



- **8/8** pass; content quality good.



---



## Next priorities (recommended order)



1. **GitHub:** `gh auth login -h github.com -p https -w` then `.\scripts\github-publish.ps1` (or `-Org otrm` for an org repo)

2. **Phase 6 — Publish:** `python -m build` then `twine upload dist/*`; tag `v0.1.0`

3. **Optional:** `.env` loading (currently env vars only; `.env` is gitignored but not auto-loaded)



---



## Out of scope for v0.1



Error interpreter, batch, presets, GUI, hardware detection — see roadmap and `.cursor/rules`.



---



## Definition of done (quick check)



- [x] Generate + explain implemented

- [x] Providers + config

- [x] pytest green

- [x] Prompt suite consistently "power-user good" (Phase 4)

- [ ] Ship to PyPI + release tag (Phase 6 — LICENSE done; publish pending)

