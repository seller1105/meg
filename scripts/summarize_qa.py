"""Summarize docs/qa-run.json for analysis.

Run after: python scripts/run_qa_suite.py
See docs/STATUS.md for latest QA findings and prompt-tuning priorities.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "docs" / "qa-run.json").read_text(encoding="utf-8"))

# Rubric entries are substring checks against the generated command line.
# A value may contain "|"-separated alternatives: any one of them counts as a hit.
RUBRIC: dict[str, list[str]] = {
    "G1": ["libx264", "aac"],
    "G2": ["-vn|-map 0:a", "pcm_s24le"],
    "G3": ["scale", "pad"],
    "G4": ["loudnorm"],
    "G5": ["concat"],
    "G6": ["copy"],
    "G7": ["subtitles"],
    "G8": ["-frames:v", "1"],
    "G9": ["yadif|bwdif"],
    "G10": ["prores"],
    "G11": [],  # IMF disclaimer is checked in the explanation text below
    "G12": ["-crf", "18", "slow"],
    "G13": ["-an"],
    "G14": ["-map"],
    "G15": ["crop"],
    # Path-based P-cases: command must reference the probed .qa-media path
    # and the derived _out output.
    "P1": [".qa-media", "_out", "libx264", "aac"],
    "P2": [".qa-media", "_out", "1920"],
    "P3": [".qa-media", "_out", "copy"],
    "P4": [".qa-media", "_out", "-map", "pcm_s24le"],
    "P5": [".qa-media", "_out", "3840"],
}

# Full-output text checks (explanations); same "|" alternative syntax.
RUBRIC_TEXT: dict[str, list[str]] = {
    "G11": ["imf"],
    "P6": ["prores", "1920x1080|1920×1080|1080p"],
}


def _hit(check: str, target: str) -> bool:
    """True when any '|'-separated alternative appears in the target."""
    lowered = target.lower()
    return any(alt.strip().lower() in lowered for alt in check.split("|"))

for r in DATA:
    cid = r["id"]
    ok = r["exit_code"] == 0
    text = (r.get("stdout") or "") + (r.get("stderr") or "")
    cmd_line = ""
    if ok and r["mode"] == "generate":
        lines = (r.get("stdout") or "").strip().splitlines()
        cmd_line = lines[0] if lines else ""
    checks = RUBRIC.get(cid, [])
    text_checks = RUBRIC_TEXT.get(cid, [])
    hits: list[str] = []
    miss: list[str] = []
    if r["mode"] == "generate" and cmd_line:
        hits += [c for c in checks if _hit(c, cmd_line)]
        miss += [c for c in checks if not _hit(c, cmd_line)]
    if text_checks:
        hits += [c for c in text_checks if _hit(c, text)]
        miss += [c for c in text_checks if not _hit(c, text)]
    rubric = f"hits={hits} miss={miss}" if (checks or text_checks) else "n/a"
    print(f"{cid} {'OK' if ok else 'FAIL'} {r['elapsed_s']}s {rubric}")
    if not ok:
        print(f"  stderr: {(r.get('stderr') or '').strip()[:200]}")
    elif r["mode"] == "generate":
        print(f"  cmd: {cmd_line[:140]}")
