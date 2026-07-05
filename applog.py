"""Simple app log: appended to story-creator.log and shown in the Log tab."""
from __future__ import annotations

import time
from pathlib import Path

LOG_FILE = Path(__file__).parent / "story-creator.log"
# size cap: when the file grows past this, the OLDEST half is dropped
# (configurable from the Log tab, persisted in settings.json)
MAX_BYTES = 2_000_000

# opt-in debug detail (toggled from the Log tab, persisted in settings.json);
# basic request/done/error lines are always logged
OPTIONS = {
    "summary_text": False,       # generated scene-summary text
    "prompts_storyboard": False,  # system + user prompt of storyboard calls
    "prompts_outline": False,     # system + user prompt of outline calls
    "prompts_scene": False,       # system + user prompt of scene-writing calls
    "prompts_summary": False,     # system + user prompt of summary calls
    "full_responses": False,      # complete response text of every call
}


def log(section: str, message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} [{section}] {message}\n"
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_BYTES:
            _trim_log()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _trim_log() -> None:
    """Drop the oldest half of the log, cutting at a line boundary."""
    text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    cut = text.find("\n", len(text) // 2)
    kept = text[cut + 1:] if cut >= 0 else text[len(text) // 2:]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    LOG_FILE.write_text(
        f"{stamp} [log] (older entries trimmed — size cap reached)\n" + kept,
        encoding="utf-8")


def read_log(max_chars: int = 200_000) -> str:
    if not LOG_FILE.exists():
        return ""
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]
    except OSError:
        return ""


def clear_log() -> None:
    try:
        LOG_FILE.write_text("", encoding="utf-8")
    except OSError:
        pass
