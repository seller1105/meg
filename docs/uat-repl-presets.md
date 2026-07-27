# UAT plan — Interactive REPL + Preset vault

Windows PowerShell, real terminal (both features are TTY-sensitive — do not run from an IDE task runner).

## Setup

```powershell
cd d:\projects\otrm\meg
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
pytest   # expect: all pass (incl. test_presets.py, test_repl.py)
```

Have ready: a small real media file, e.g. `D:\media\test clip.mov` (path with a space, on purpose). Back up or ignore `~\.meg\presets.toml` if it exists.

---

## A. REPL entry & basics

| # | Steps | Expected |
|---|-------|----------|
| A1 | Run bare `meg` in a terminal | Banner "meg — interactive session…", `meg>` prompt. No help dump |
| A2 | Type `help` | Command list incl. `preset run`, `save <nickname>` |
| A3 | Press Enter on empty line, then type spaces + Enter | Prompt repeats, no error |
| A4 | Type `exit` (repeat test with `q` and Ctrl+C) | Clean exit, no traceback |
| A5 | `meg --help` | Normal help, incl. `preset` subcommand |
| A6 | `echo x \| meg` (non-TTY) | Prints help, exits 0, no hang |

## B. REPL generate / refine / run

| # | Steps | Expected |
|---|-------|----------|
| B1 | `meg>` type `convert mkv to h264 mp4` | Command + explanation, then `[r]un  [e]dit  [s]ave preset  [q]uit` |
| B2 | Choose `e`, give feedback "use crf 18" | Revised command, menu again |
| B3 | Type request with your real file: `convert "D:\media\test clip.mov" to 720p` | Command uses the probed file; explanation cites source specs; output path is `test clip_out.mov`-style, never the input |
| B4 | Choose `r`, approve `y` | Live progress; output file created; back at `meg>` afterwards |
| B5 | New request after B4 | Second generate works in same session (probe cache: no visible re-probe delay for same file) |
| B6 | Choose `q` at menu | Hint: "Type 'save <nickname>'…", back at `meg>` |

## C. Saving presets

| # | Steps | Expected |
|---|-------|----------|
| C1 | After B6, type `save proxy720 quick 720p proxy` | "Saved preset 'proxy720'"; command shown with `{input}`/`{output}` placeholders |
| C2 | `save proxy720 again` | Error: already exists (session continues) |
| C3 | `save bad name!` | Nickname validation error |
| C4 | Fresh REPL, `save x` before generating | "Nothing to save yet" |
| C5 | One-shot flow: `meg "convert mkv to mp4"`, choose `s`, nickname `oneshot`, blank description | Saved; menu returns; `q` exits |
| C6 | Open `~\.meg\presets.toml` | Human-readable TOML; paths templatized; extension recorded |

## D. Preset CLI

| # | Steps | Expected |
|---|-------|----------|
| D1 | `meg preset list` | Both presets with nickname, description, template |
| D2 | `meg preset search proxy` / `meg preset search libx264` / `meg preset search nomatch` | Matches by nickname and command text; no-match exits 1 |
| D3 | `meg preset save dup "not an ffmpeg command"` | Rejected (allowlist), exit 1 |
| D4 | `meg preset save proxy720 "ffmpeg -i a.mov out.mp4" --overwrite` | Overwrite succeeds |
| D5 | `meg preset delete oneshot`, then `meg preset delete oneshot` | First succeeds; second errors, exit 1 |

## E. Preset run (the core promise)

| # | Steps | Expected |
|---|-------|----------|
| E1 | `meg preset run proxy720 "D:\media\test clip.mov"` | Rendered command shows the new input; output beside it with preset's extension; preflight runs; approval prompt |
| E2 | Approve `y` | Encode runs with progress; correct output file |
| E3 | Re-run E1, approve with output already existing | Overwrite warning + confirmation before running |
| E4 | `meg preset run proxy720 missing.mov` | Preflight error (missing input), nothing executed, exit 1 |
| E5 | `meg preset run nope clip.mov` | "No preset named 'nope'", exit 1 |
| E6 | Save a 2-input preset (mux video+audio), run with 1 file | "expects 2 input files" error |
| E7 | From REPL: `preset run proxy720 "D:\media\test clip.mov"`, decline `n` | Same flow inside session; declining returns to `meg>` |

## F. Resilience

| # | Steps | Expected |
|---|-------|----------|
| F1 | Clear both API keys in a fresh shell, run `meg`, type a request | "No API key found" error, but session stays alive; `presets` / `preset run` still work |
| F2 | Corrupt `presets.toml` (add garbage line), run `meg preset list` | Clear "Could not read preset vault" error, no traceback |
| F3 | Nickname/description with quotes: `-d "has ""quotes"""` | Saves and lists correctly (TOML escaping) |

## Sign-off

- [ ] Sections A–F pass on Windows
- [ ] `pytest` green locally
- [ ] Move REPL + preset-vault board tasks In Review → Done, commit
