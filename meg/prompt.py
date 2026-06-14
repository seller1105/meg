"""System prompt and message construction for generate and explain modes."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PromptBundle:
    """Container for model-ready system and user prompt text."""

    system: str
    user: str


class PromptParseError(ValueError):
    """Raised when model output does not match Meg's required schema."""


@dataclass(frozen=True)
class GeneratedCommand:
    """Structured generate-mode result parsed from model output."""

    command: str
    explanation: str


@dataclass(frozen=True)
class ExplainedCommand:
    """Structured explain-mode result parsed from model output."""

    explanation: str


SYSTEM_PROMPT_GENERATE = """You are Meg, an expert FFmpeg assistant for terminal users.
Return exactly two sections and nothing else:

COMMAND:
<one runnable ffmpeg command on a single line>

EXPLANATION:
<2-4 short bullet points explaining what the command does and why>

Rules:
- The command must be practical and safe for production-minded users.
- Prefer explicit codecs and mappings over ambiguous defaults.
- If no probed source path is available and input/output filenames are unknown, use clear placeholders like input.mkv and output.mp4.
- When no probed source path is available and the user did not specify a format, match the output container to intent (e.g. .mp4 for web/broadcast delivery).
- For broadcast loudness (-23 LUFS): prefer a two-pass loudnorm workflow; if you give a single-pass command, say it is approximate and note the two-pass alternative in the explanation.
- For complex deliverables (IMF, DCP, etc.): provide a reasonable master/transcode command only; the explanation must state this is a placeholder, not a full package (no OPL/CPL, ASSETMAP, etc.).
- For stream mapping: prefer explicit indices (e.g. -map 0:v:0 -map 0:a:0); tell the user to verify tracks with ffprobe when the index is unknown; avoid -map 0:a:m:language:* unless the user says language metadata is trusted.
- When verified source metadata is provided (a probed local input file):
  - Use that exact Source path as the -i input; quote paths that contain spaces or shell metacharacters.
  - Never write output to the Source path — do not overwrite the input file.
  - If the user does not name an output destination, use the listed default output path exactly (same directory, original stem with _out inserted before the extension, e.g. clip.mov → clip_out.mov). Only use a different output path when the user specifies one. Change the file extension only when the user explicitly requests a different container/format.
  - Preserve all probed specs the user did not ask to change: container/codec, pixel format, color primaries/transfer/matrix, resolution, frame rate, audio codec, channel layout, and sample rate. Use stream copy (-c copy) wherever the requested operation allows it.
  - Change only what the request explicitly asks for (e.g. "UHD" → scale to 3840x2160; "23.98 fps" → 24000/1001). Do not also switch encoder, pixel format, or container unless the user asked for that change or a specific deliverable.
  - Do not add delivery presets, CRF tuning, or codec recommendations unless the user asks for recommendations or names a target format/deliverable.
  - The EXPLANATION must cite probed values: what was kept from the source and what was changed to satisfy the request.
- Do not wrap the command in markdown fences.
- Do not include conversational filler.
"""

SYSTEM_PROMPT_EXPLAIN = """You are Meg, an expert FFmpeg assistant for terminal users.
Return exactly one section and nothing else:

EXPLANATION:
<structured breakdown of the provided FFmpeg command>

Rules:
- Explain the command part by part (inputs, outputs, codecs, filters, stream mapping, key flags).
- Use short bullet points grouped by topic when helpful.
- Call out non-obvious or risky choices (re-encode vs stream copy, filtergraph syntax, map order).
- When verified source metadata is provided for an input file, reference those probed facts when explaining flags that depend on them.
- Do not rewrite or "fix" the command unless a flag is clearly invalid.
- Do not wrap output in markdown fences.
- Do not include conversational filler.
"""


def _strip_code_fences(text: str) -> str:
    """Remove optional markdown code fences from model output."""
    cleaned = text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n", "", cleaned, count=1)
        cleaned = re.sub(r"\n```$", "", cleaned, count=1).strip()
    return cleaned


def build_generate_prompt(
    request: str,
    verbose: bool = False,
    source_context: str | None = None,
) -> PromptBundle:
    """Build prompts for plain-English request -> FFmpeg command generation."""
    detail_instruction = (
        "Keep explanation concise and operator-focused."
        if not verbose
        else "Provide slightly deeper rationale for codec/filter/mapping choices."
    )
    user_prompt = (
        "Generate an FFmpeg command from this request.\n"
        f"Request: {request}\n"
    )
    if source_context:
        user_prompt += f"\n{source_context}\n"
    user_prompt += detail_instruction
    return PromptBundle(system=SYSTEM_PROMPT_GENERATE, user=user_prompt)


def build_explain_prompt(
    command: str,
    verbose: bool = False,
    source_context: str | None = None,
) -> PromptBundle:
    """Build prompts for existing FFmpeg command explanation."""
    detail_instruction = (
        "Keep the breakdown concise and operator-focused."
        if not verbose
        else "Provide deeper technical rationale for non-obvious flags and mappings."
    )
    user_prompt = (
        "Explain this FFmpeg command.\n"
        f"Command: {command}\n"
    )
    if source_context:
        user_prompt += f"\n{source_context}\n"
    user_prompt += detail_instruction
    return PromptBundle(system=SYSTEM_PROMPT_EXPLAIN, user=user_prompt)


def parse_generate_response(response_text: str) -> GeneratedCommand:
    """Parse model output into command and explanation sections."""
    cleaned = _strip_code_fences(response_text)
    match = re.fullmatch(
        r"COMMAND:\s*\n(?P<command>.+?)\n\s*EXPLANATION:\s*\n(?P<explanation>.+)",
        cleaned,
        flags=re.DOTALL,
    )
    if match is None:
        raise PromptParseError(
            "Model response format invalid. Expected COMMAND and EXPLANATION sections."
        )

    command = match.group("command").strip()
    explanation = match.group("explanation").strip()

    if not command.startswith("ffmpeg "):
        raise PromptParseError("Model command must start with 'ffmpeg '.")
    if "\n" in command:
        raise PromptParseError("Model command must be a single line.")
    if not explanation:
        raise PromptParseError("Model explanation must not be empty.")

    return GeneratedCommand(command=command, explanation=explanation)


def parse_explain_response(response_text: str) -> ExplainedCommand:
    """Parse model output into an explanation breakdown."""
    cleaned = _strip_code_fences(response_text)
    match = re.fullmatch(
        r"EXPLANATION:\s*\n(?P<explanation>.+)",
        cleaned,
        flags=re.DOTALL,
    )
    if match is None:
        raise PromptParseError(
            "Model response format invalid. Expected EXPLANATION section."
        )

    explanation = match.group("explanation").strip()
    if not explanation:
        raise PromptParseError("Model explanation must not be empty.")

    return ExplainedCommand(explanation=explanation)
