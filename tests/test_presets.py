"""Tests for the preset vault (meg/presets.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from meg.presets import (
    Preset,
    PresetError,
    add_preset,
    delete_preset,
    get_preset,
    load_presets,
    make_template_argv,
    render_preset_command,
    search_presets,
    validate_nickname,
)

_CMD = 'ffmpeg -i input.mov -c:v libx264 -crf 18 -c:a aac output.mp4'


def _vault(tmp_path: Path) -> Path:
    return tmp_path / "presets.toml"


# --- nickname validation -------------------------------------------------


def test_validate_nickname_accepts_simple_names() -> None:
    assert validate_nickname("proxy-1080") == "proxy-1080"
    assert validate_nickname("  h264_web  ") == "h264_web"


@pytest.mark.parametrize("bad", ["", "  ", "-leading", "has space", "a" * 65, "nick!name"])
def test_validate_nickname_rejects_invalid(bad: str) -> None:
    with pytest.raises(PresetError):
        validate_nickname(bad)


# --- templatization ------------------------------------------------------


def test_make_template_replaces_input_and_output() -> None:
    template = make_template_argv(
        ["ffmpeg", "-i", "input.mov", "-c:v", "libx264", "output.mp4"]
    )
    assert template == ("ffmpeg", "-i", "{input}", "-c:v", "libx264", "{output}")


def test_make_template_rejects_non_ffmpeg() -> None:
    with pytest.raises(PresetError):
        make_template_argv(["ffprobe", "-i", "input.mov"])


def test_make_template_handles_two_inputs() -> None:
    template = make_template_argv(
        ["ffmpeg", "-i", "video.mp4", "-i", "audio.wav", "-c", "copy", "muxed.mkv"]
    )
    assert "{input}" in template
    assert "{input2}" in template
    assert "{output}" in template


# --- save / load roundtrip ----------------------------------------------


def test_add_and_get_preset_roundtrip(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    saved = add_preset("proxy", _CMD, description="1080 proxy", path=vault)
    assert saved.argv[2] == "{input}"

    loaded = get_preset("proxy", path=vault)
    assert loaded.nickname == "proxy"
    assert loaded.description == "1080 proxy"
    assert loaded.argv == saved.argv
    assert loaded.output_ext == ".mp4"
    assert loaded.created  # ISO date recorded


def test_add_preset_rejects_duplicate_without_overwrite(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    add_preset("proxy", _CMD, path=vault)
    with pytest.raises(PresetError, match="already exists"):
        add_preset("proxy", _CMD, path=vault)
    add_preset("proxy", _CMD, description="v2", overwrite=True, path=vault)
    assert get_preset("proxy", path=vault).description == "v2"


def test_add_preset_rejects_non_allowlisted_executable(tmp_path: Path) -> None:
    with pytest.raises(PresetError):
        add_preset("bad", "rm -rf /", path=_vault(tmp_path))


def test_load_presets_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_presets(_vault(tmp_path)) == {}


def test_vault_roundtrips_special_characters(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    command = 'ffmpeg -i "my clip.mov" -vf scale=1920:1080 "my clip out.mp4"'
    add_preset("spaces", command, description='has "quotes" and \\slashes\\', path=vault)
    loaded = get_preset("spaces", path=vault)
    assert loaded.description == 'has "quotes" and \\slashes\\'
    assert "{input}" in loaded.argv


# --- delete / search -----------------------------------------------------


def test_delete_preset(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    add_preset("proxy", _CMD, path=vault)
    delete_preset("proxy", path=vault)
    assert load_presets(vault) == {}
    with pytest.raises(PresetError, match="No preset named"):
        delete_preset("proxy", path=vault)


def test_search_matches_nickname_description_and_command(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    add_preset("proxy", _CMD, description="editorial proxy", path=vault)
    add_preset(
        "loudnorm",
        "ffmpeg -i in.wav -af loudnorm=I=-16 out.wav",
        description="podcast audio",
        path=vault,
    )

    assert [p.nickname for p in search_presets("proxy", path=vault)] == ["proxy"]
    assert [p.nickname for p in search_presets("PODCAST", path=vault)] == ["loudnorm"]
    assert [p.nickname for p in search_presets("libx264", path=vault)] == ["proxy"]
    assert search_presets("nomatch", path=vault) == []


# --- rendering against new inputs ---------------------------------------


def test_render_substitutes_new_input_and_derives_output(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    add_preset("proxy", _CMD, path=vault)
    preset = get_preset("proxy", path=vault)

    command = render_preset_command(preset, ["D:/media/new clip.mov"])
    assert "new clip.mov" in command
    assert "new clip_out.mp4" in command  # preset extension preserved
    assert "input.mov" not in command
    assert "output.mp4" not in command


def test_render_rejects_wrong_input_count(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    add_preset("proxy", _CMD, path=vault)
    preset = get_preset("proxy", path=vault)
    with pytest.raises(PresetError, match="expects 1 input"):
        render_preset_command(preset, ["a.mov", "b.mov"])


def test_render_two_input_preset(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    add_preset(
        "mux",
        "ffmpeg -i video.mp4 -i audio.wav -c copy muxed.mkv",
        path=vault,
    )
    preset = get_preset("mux", path=vault)
    command = render_preset_command(preset, ["v2.mp4", "a2.wav"])
    assert "v2.mp4" in command
    assert "a2.wav" in command
    assert "v2_out.mkv" in command


def test_preset_input_count() -> None:
    preset = Preset(
        nickname="x",
        argv=("ffmpeg", "-i", "{input}", "-i", "{input2}", "{output}"),
    )
    assert preset.input_count == 2
