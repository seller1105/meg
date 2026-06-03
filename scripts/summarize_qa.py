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

RUBRIC: dict[str, list[str]] = {
    "G1": ["libx264", "aac"],
    "G2": ["-vn", "pcm_s24le"],
    "G3": ["scale", "pad"],
    "G4": ["loudnorm"],
    "G5": ["concat"],
    "G6": ["copy"],
    "G7": ["subtitles"],
    "G8": ["-frames:v", "1"],
    "G9": ["yadif", "bwdif"],
    "G10": ["prores"],
    "G11": ["imf"],
    "G12": ["-crf", "18", "slow"],
    "G13": ["-an"],
    "G14": ["-map"],
    "G15": ["crop"],
}

for r in DATA:
    cid = r["id"]
    ok = r["exit_code"] == 0
    text = (r.get("stdout") or "") + (r.get("stderr") or "")
    cmd_line = ""
    if ok and r["mode"] == "generate":
        lines = (r.get("stdout") or "").strip().splitlines()
        cmd_line = lines[0] if lines else ""
    checks = RUBRIC.get(cid, [])
    if r["mode"] == "generate" and cmd_line:
        hits = [c for c in checks if c.lower() in cmd_line.lower()]
        miss = [c for c in checks if c.lower() not in cmd_line.lower()]
        rubric = f"hits={hits} miss={miss}"
    else:
        rubric = "n/a"
    print(f"{cid} {'OK' if ok else 'FAIL'} {r['elapsed_s']}s {rubric}")
    if not ok:
        print(f"  stderr: {(r.get('stderr') or '').strip()[:200]}")
    elif r["mode"] == "generate":
        print(f"  cmd: {cmd_line[:140]}")
