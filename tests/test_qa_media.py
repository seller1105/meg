"""Tests for the path-based QA suite helpers (no ffmpeg execution)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # so run_qa_suite can `import make_qa_media`
    spec.loader.exec_module(module)
    return module


make_qa_media = _load("make_qa_media")
run_qa_suite = _load("run_qa_suite")


def test_media_specs_shape() -> None:
    specs = make_qa_media.media_specs()
    assert len(specs) == 3
    for path, argv in specs:
        assert path.parent == make_qa_media.MEDIA_DIR
        assert argv[0] == "ffmpeg"
        assert str(path) == argv[-1]  # output last, argv-only
        assert "-y" in argv  # script owns its output dir


def test_media_specs_cover_key_variants() -> None:
    specs = {path.name: argv for path, argv in make_qa_media.media_specs()}

    # A filename with a space, to exercise quoting end to end.
    assert any(" " in name for name in specs)

    master = specs["qa master 1080p25.mov"]
    assert "prores_ks" in master
    assert "yuv422p10le" in master

    clip = specs["clip_720p2398.mp4"]
    assert "libx264" in clip
    assert any("24000/1001" in token for token in clip)

    two_audio = specs["two_audio.mkv"]
    assert two_audio.count("-map") == 3  # 1 video + 2 audio tracks


def test_pathbased_cases_reference_media_files() -> None:
    generate, explain = run_qa_suite._pathbased_cases()
    ids = [cid for cid, _ in generate] + [cid for cid, _ in explain]
    assert ids == ["P1", "P2", "P3", "P4", "P5", "P6"]

    media_dir = str(make_qa_media.MEDIA_DIR)
    for _, prompt in generate + explain:
        assert media_dir in prompt
    # Paths with spaces must be quoted in the prompts.
    assert any('"' in prompt and "qa master" in prompt for _, prompt in generate)


def test_pathbased_prompts_are_quoted() -> None:
    generate, _ = run_qa_suite._pathbased_cases()
    for _, prompt in generate:
        # every embedded path is wrapped in double quotes
        assert prompt.count('"') >= 2


def test_run_meg_does_not_inherit_stdin() -> None:
    """Regression: the QA runner hung forever when meg saw a TTY on stdin.

    Launch a child that tries to read stdin; with DEVNULL it must get EOF
    immediately instead of blocking.
    """
    proc = run_qa_suite._run_meg(
        [
            sys.executable,
            "-c",
            "import sys; data = sys.stdin.read(); print('EOF' if data == '' else 'GOT-INPUT')",
        ]
    )
    assert proc.returncode == 0
    assert "EOF" in proc.stdout
