"""Preset vault — save, nickname, search, and reuse ffmpeg commands.

Presets are stored in ``~/.meg/presets.toml``. Input and output paths in a
saved command are replaced with ``{input}`` / ``{input2}`` / ``{output}``
placeholders so a preset can be re-applied to any source file later. The
output path is re-derived from the new input at run time, so presets never
point at stale outputs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

import tomllib

from meg.exec import (
    CommandValidationError,
    analyze_ffmpeg_safety,
    format_argv_display,
    parse_command_line,
    validate_allowed_executable,
)
from meg.ffprobe import default_output_path

_NICKNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_INPUT_PLACEHOLDER = "{input}"
_OUTPUT_PLACEHOLDER = "{output}"
_NUMBERED_INPUT = re.compile(r"\{input(\d+)\}")


class PresetError(ValueError):
    """Raised for invalid preset operations (bad nickname, missing preset, ...)."""


@dataclass(frozen=True)
class Preset:
    """A saved, reusable command template."""

    nickname: str
    argv: tuple[str, ...]
    description: str = ""
    created: str = ""
    output_ext: str = ""

    @property
    def input_count(self) -> int:
        count = 0
        for token in self.argv:
            if token == _INPUT_PLACEHOLDER or _NUMBERED_INPUT.fullmatch(token):
                count += 1
        return count

    @property
    def display(self) -> str:
        return format_argv_display(self.argv)


def presets_path() -> Path:
    """Location of the preset vault file."""
    return Path.home() / ".meg" / "presets.toml"


def validate_nickname(nickname: str) -> str:
    """Validate and return a normalized preset nickname."""
    candidate = nickname.strip()
    if not _NICKNAME_PATTERN.match(candidate):
        raise PresetError(
            "Nicknames must be 1-64 characters: letters, digits, '-' or '_', "
            "starting with a letter or digit."
        )
    return candidate


def _input_placeholder(index: int) -> str:
    return _INPUT_PLACEHOLDER if index == 0 else f"{{input{index + 1}}}"


def make_template_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Replace input/output path tokens with placeholders.

    Raises PresetError when the command has no recognizable input, or is not
    a simple single-output ffmpeg command.
    """
    safety = analyze_ffmpeg_safety(argv)
    if safety is None:
        raise PresetError("Only ffmpeg commands can be saved as presets.")
    if safety.ambiguous or len(safety.output_paths) > 1:
        raise PresetError(
            "Commands with multiple or ambiguous outputs can't be saved as presets."
        )
    if not safety.input_paths:
        raise PresetError("Could not find an input file (-i) in the command.")

    template = list(argv)
    for index, input_path in enumerate(safety.input_paths):
        replaced = False
        for pos, token in enumerate(template):
            if token == input_path and pos > 0 and template[pos - 1] == "-i":
                template[pos] = _input_placeholder(index)
                replaced = True
                break
        if not replaced:
            raise PresetError(f"Could not templatize input path: {input_path}")

    for output_path in safety.output_paths:
        for pos in range(len(template) - 1, 0, -1):
            if template[pos] == output_path:
                template[pos] = _OUTPUT_PLACEHOLDER
                break

    return tuple(template)


def render_preset_command(preset: Preset, inputs: Sequence[str]) -> str:
    """Substitute real paths into a preset template and return a command string.

    The output path is derived from the first input via default_output_path.
    The preset's recorded output extension is preserved, so an mp4 preset
    applied to input.mov still produces an .mp4.
    """
    expected = preset.input_count
    if len(inputs) != expected:
        noun = "file" if expected == 1 else "files"
        raise PresetError(
            f"Preset '{preset.nickname}' expects {expected} input {noun}, "
            f"got {len(inputs)}."
        )

    argv: list[str] = []
    for token in preset.argv:
        numbered = _NUMBERED_INPUT.fullmatch(token)
        if token == _INPUT_PLACEHOLDER:
            argv.append(inputs[0])
        elif numbered:
            argv.append(inputs[int(numbered.group(1)) - 1])
        elif token == _OUTPUT_PLACEHOLDER:
            derived = default_output_path(inputs[0])
            if preset.output_ext:
                derived = str(Path(derived).with_suffix(preset.output_ext))
            argv.append(derived)
        else:
            argv.append(token)
    return format_argv_display(argv)


def _toml_string(value: str) -> str:
    """Encode a string as a TOML basic string (JSON escaping is compatible)."""
    return json.dumps(value, ensure_ascii=False)


def load_presets(path: Path | None = None) -> dict[str, Preset]:
    """Load all presets from the vault, keyed by nickname."""
    vault_path = path or presets_path()
    if not vault_path.exists():
        return {}
    try:
        with vault_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise PresetError(f"Could not read preset vault '{vault_path}': {exc}") from exc

    presets: dict[str, Preset] = {}
    for nickname, entry in data.get("presets", {}).items():
        if not isinstance(entry, dict):
            continue
        argv = entry.get("argv")
        if not isinstance(argv, list) or not all(isinstance(t, str) for t in argv):
            continue
        preset = Preset(
            nickname=str(nickname),
            argv=tuple(argv),
            description=str(entry.get("description", "")),
            created=str(entry.get("created", "")),
            output_ext=str(entry.get("output_ext", "")),
        )
        presets[preset.nickname] = preset
    return presets


def save_presets(presets: dict[str, Preset], path: Path | None = None) -> None:
    """Write the vault file (write to a temp file, then replace)."""
    vault_path = path or presets_path()
    vault_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "# Meg preset vault - managed by `meg preset` commands.",
        "",
    ]
    for nickname in sorted(presets):
        preset = presets[nickname]
        lines.append(f"[presets.{_toml_string(nickname)}]")
        argv_items = ", ".join(_toml_string(token) for token in preset.argv)
        lines.append(f"argv = [{argv_items}]")
        lines.append(f"description = {_toml_string(preset.description)}")
        lines.append(f"created = {_toml_string(preset.created)}")
        if preset.output_ext:
            lines.append(f"output_ext = {_toml_string(preset.output_ext)}")
        lines.append("")

    tmp_path = vault_path.with_suffix(".toml.tmp")
    tmp_path.write_text("\n".join(lines), encoding="utf-8")
    tmp_path.replace(vault_path)


def add_preset(
    nickname: str,
    command: str,
    *,
    description: str = "",
    overwrite: bool = False,
    path: Path | None = None,
) -> Preset:
    """Validate, templatize, and store a command as a preset."""
    name = validate_nickname(nickname)

    try:
        parsed = parse_command_line(command)
        validate_allowed_executable(parsed.argv)
    except CommandValidationError as exc:
        raise PresetError(str(exc)) from exc

    template = make_template_argv(parsed.argv)

    safety = analyze_ffmpeg_safety(parsed.argv)
    output_ext = ""
    if safety is not None and safety.output_paths:
        output_ext = Path(safety.output_paths[0]).suffix

    presets = load_presets(path)
    if name in presets and not overwrite:
        raise PresetError(
            f"Preset '{name}' already exists. Use --overwrite to replace it."
        )

    preset = Preset(
        nickname=name,
        argv=template,
        description=description.strip(),
        created=date.today().isoformat(),
        output_ext=output_ext,
    )
    presets[name] = preset
    save_presets(presets, path)
    return preset


def get_preset(nickname: str, path: Path | None = None) -> Preset:
    """Fetch one preset by nickname."""
    presets = load_presets(path)
    preset = presets.get(nickname.strip())
    if preset is None:
        raise PresetError(
            f"No preset named '{nickname.strip()}'. Try 'meg preset list'."
        )
    return preset


def delete_preset(nickname: str, path: Path | None = None) -> None:
    """Remove a preset from the vault."""
    presets = load_presets(path)
    name = nickname.strip()
    if name not in presets:
        raise PresetError(f"No preset named '{name}'.")
    del presets[name]
    save_presets(presets, path)


def search_presets(query: str, path: Path | None = None) -> list[Preset]:
    """Case-insensitive substring search over nickname, description, command."""
    needle = query.strip().lower()
    results: list[Preset] = []
    for preset in load_presets(path).values():
        haystack = " ".join(
            (preset.nickname, preset.description, preset.display)
        ).lower()
        if needle in haystack:
            results.append(preset)
    return sorted(results, key=lambda p: p.nickname)
