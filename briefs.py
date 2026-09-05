"""Brief generation for single-output stories.

A brief is a (system prompt, user prompt) pair written by a small model from
the user's concept. The system prompt is a writing style guide; the user
prompt is a scene-by-scene creative brief that withholds specifics on purpose,
so the big model invents them while writing. The pair is then handed to the
writing model verbatim, in one call, and it returns the whole story.

The instruction texts and the response parser are ported from the author's
earlier app (storyteller-concept/outline_client.py), where they were tuned
against real model output over many runs.
"""
from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from pathlib import Path

from project import (
    APP_DIR,
    BRIEFS_DIR,
    BRIEF_INSTRUCTIONS_DIR,
    SYSTEM_PROMPTS_DIR,
    USER_PROMPTS_DIR,
    slugify,
    unique_path,
)

_BUILTIN_FILE = APP_DIR / "brief_instructions.json"


class BriefError(Exception):
    pass


# -- the instruction library ------------------------------------------------
# Built-ins live in a data file rather than in this module: they are ~52 KB of
# prose, and keeping them as JSON makes them readable and hand-editable.

def _load_builtins() -> "OrderedDict[str, str]":
    try:
        data = json.loads(_BUILTIN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return OrderedDict()
    return OrderedDict((str(k), str(v)) for k, v in data.items())


BUILTIN_INSTRUCTIONS: "OrderedDict[str, str]" = _load_builtins()
DEFAULT_INSTRUCTION_NAME = "Creative Brief (genre-aware)"


# Appended to whichever instruction is used, built-in or your own. Each rule
# comes from a specific failure in a finished single-output story: a model
# that runs out of plot before it runs out of budget pads by restating what
# the reader already knows; it reports its ending instead of playing it out;
# and it is asked for the aftermath of an event no beat ever covers, so the
# story stops without the thing it was building towards ever happening.
BRIEF_RULES_SUFFIX = (
    "\n\n--- ADDITIONAL REQUIREMENTS ---\n"
    "Apply these on top of everything above.\n\n"
    "The SYSTEM PROMPT you produce must also contain these rules:\n"
    "  - Never restate a fact, deduction or conclusion the reader has already "
    "been given. Every scene must add something new. If a character explains "
    "an event the reader watched happen, cut the explanation.\n"
    "  - Do not summarise the story inside the story. No character recaps the "
    "evidence, the plan or the situation for another character who was there.\n"
    "  - Play the ending out in scene, with the people present speaking and "
    "acting. Never close by reporting what happened afterwards.\n\n"
    "The USER PROMPT you produce must also:\n"
    "  - Make the final beat a scene acted out between characters face to "
    "face — a confrontation, a reckoning, a decision taken in front of us — "
    "never a phone call, a report or a summary of the outcome.\n"
    "  - Require that any hidden reason driving the story is revealed on the "
    "page. If a character has been concealing why they acted, the story must "
    "show that reason, not leave it open.\n"
    "  - Keep every beat pointed at something that has not happened yet, so "
    "the story never runs out of events before it runs out of length.\n"
    "  - List a beat for every event the story needs, including any "
    "event the final scene reacts to. If the closing scene is set after "
    "a fight, a trial, a wedding or a funeral, an earlier beat must be "
    "that fight, trial, wedding or funeral. Never ask for the aftermath "
    "of something the beats never cover.\n\n"
    "Output only the raw JSON object with the two keys. No other text."
)


def list_instructions() -> list[str]:
    """Built-in instruction names first, then the user's own .txt files."""
    names = list(BUILTIN_INSTRUCTIONS.keys())
    if BRIEF_INSTRUCTIONS_DIR.is_dir():
        for p in sorted(BRIEF_INSTRUCTIONS_DIR.glob("*.txt")):
            if p.stem not in names:
                names.append(p.stem)
    return names


def get_instruction(name: str) -> str:
    """The instruction text for a name, falling back to the default."""
    if name in BUILTIN_INSTRUCTIONS:
        return BUILTIN_INSTRUCTIONS[name]
    path = BRIEF_INSTRUCTIONS_DIR / f"{name}.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return BUILTIN_INSTRUCTIONS.get(DEFAULT_INSTRUCTION_NAME, "")


def is_builtin(name: str) -> bool:
    return name in BUILTIN_INSTRUCTIONS


def save_custom_instruction(name: str, text: str) -> None:
    BRIEF_INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEF_INSTRUCTIONS_DIR / f"{slugify(name)}.txt").write_text(
        text.strip(), encoding="utf-8")


def delete_custom_instruction(name: str) -> None:
    """Built-ins cannot be deleted — they are the shipped library."""
    if is_builtin(name):
        return
    path = BRIEF_INSTRUCTIONS_DIR / f"{name}.txt"
    if path.is_file():
        path.unlink()


# -- parsing what the model sent back ---------------------------------------

def _extract_pair(obj) -> dict | None:
    if not isinstance(obj, dict):
        return None
    sp = obj.get("system_prompt")
    up = obj.get("user_prompt")
    if isinstance(sp, str) and isinstance(up, str):
        return {"system_prompt": sp.strip(), "user_prompt": up.strip()}
    return None


def parse_brief_response(raw: str) -> dict:
    """Pull {"system_prompt", "user_prompt"} out of whatever the model sent.

    Models return this pair in a remarkable number of shapes, and every branch
    below exists because one of them actually happened: bare JSON, JSON in
    markdown fences, JSON buried in prose, a second JSON object nested inside
    user_prompt, two separate half-objects, and a single object whose inner
    commas defeat json.loads.
    """
    text = raw.strip()

    fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence:
        text = fence.group(1).strip()

    # 1: the whole thing is the JSON object
    try:
        pair = _extract_pair(json.loads(text))
        if pair:
            try:
                inner = _extract_pair(json.loads(pair["user_prompt"]))
                if inner:
                    return inner
            except Exception:
                pass
            return pair
    except json.JSONDecodeError:
        pass

    # 2: a JSON object somewhere inside surrounding prose
    candidates = []
    for m in re.finditer(r"\{[\s\S]+?\}", text):
        try:
            pair = _extract_pair(json.loads(m.group(0)))
        except json.JSONDecodeError:
            continue
        if pair:
            candidates.append(pair)
    if candidates:
        candidates.sort(key=lambda p: 0 if p["system_prompt"] else 1)
        best = candidates[0]
        try:
            inner = _extract_pair(json.loads(best["user_prompt"]))
            if inner:
                return inner
        except Exception:
            pass
        return best

    # 3: two separate objects, one carrying each key
    sys_text = usr_text = None
    for m in re.finditer(r"\{[\s\S]+?\}", text):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if isinstance(obj.get("system_prompt"), str):
                sys_text = obj["system_prompt"].strip()
            if isinstance(obj.get("user_prompt"), str):
                usr_text = obj["user_prompt"].strip()
    if sys_text is not None or usr_text is not None:
        return {"system_prompt": sys_text or "", "user_prompt": usr_text or ""}

    # 4: both keys present but json.loads choked on the inner punctuation
    m = re.search(
        r'"system_prompt"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*'
        r'"user_prompt"\s*:\s*"((?:[^"\\]|\\.)*)"',
        text, re.DOTALL)
    if m:
        try:
            return {"system_prompt": json.loads('"' + m.group(1) + '"').strip(),
                    "user_prompt": json.loads('"' + m.group(2) + '"').strip()}
        except Exception:
            pass

    # nothing parsed: keep the raw text as the user prompt so the run is
    # salvageable by hand rather than silently empty
    return {"system_prompt": "", "user_prompt": raw.strip()}


# -- hand-written prompt files ----------------------------------------------
# A brief is one JSON file holding both prompts. Some prompts are written by
# hand and kept as plain .txt instead — a system prompt and a user prompt in
# separate files, mixed and matched freely. Both feed the same writing call.

SYSTEM = "system"
USER = "user"


def _prompt_dir(kind: str) -> Path:
    return SYSTEM_PROMPTS_DIR if kind == SYSTEM else USER_PROMPTS_DIR


def list_prompt_files(kind: str) -> list[str]:
    """Names (no extension) of the .txt prompts of one kind, A-Z."""
    directory = _prompt_dir(kind)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.txt"))


def load_prompt_file(kind: str, name: str) -> str:
    path = _prompt_dir(kind) / f"{name}.txt"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_prompt_file(kind: str, name: str, text: str) -> Path:
    """Write a prompt to its folder; returns the path actually used."""
    directory = _prompt_dir(kind)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slugify(name)}.txt"
    path.write_text(text.strip(), encoding="utf-8")
    return path


def delete_prompt_file(kind: str, name: str) -> None:
    path = _prompt_dir(kind) / f"{name}.txt"
    if path.is_file():
        path.unlink()


# -- the saved brief library ------------------------------------------------

@dataclass
class Brief:
    """A generated (or hand-written) prompt pair, saved for reuse."""
    title: str = ""
    concept: str = ""
    instruction_name: str = ""
    system_prompt: str = ""
    user_prompt: str = ""
    created: float = field(default_factory=time.time)
    gen_info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Brief":
        b = cls()
        for k, v in (d or {}).items():
            if hasattr(b, k):
                setattr(b, k, v)
        return b

    @property
    def is_usable(self) -> bool:
        """A brief with no user prompt cannot write anything."""
        return bool(self.user_prompt.strip())


def _title_for(concept: str, fallback: str = "brief") -> str:
    first = " ".join(concept.split())[:60].strip()
    return first or fallback


def save_brief(brief: Brief) -> Path:
    """Write a brief to briefs/ under a dated filename; returns the path."""
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    if not brief.title:
        brief.title = _title_for(brief.concept)
    stem = f"brief_{slugify(brief.title)}_{time.strftime('%Y-%m-%d_%H%M')}"
    path = unique_path(BRIEFS_DIR, stem, ".json")
    path.write_text(json.dumps(brief.to_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def list_briefs() -> list[str]:
    """Saved brief filenames, newest first."""
    if not BRIEFS_DIR.is_dir():
        return []
    files = sorted(BRIEFS_DIR.glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in files]


def load_brief(filename: str) -> Brief | None:
    path = BRIEFS_DIR / filename
    if not path.is_file():
        return None
    try:
        return Brief.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def overwrite_brief(filename: str, brief: Brief) -> None:
    """Save edits back to an existing brief file."""
    path = BRIEFS_DIR / filename
    if path.is_file():
        path.write_text(json.dumps(brief.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8")


def delete_brief(filename: str) -> None:
    path = BRIEFS_DIR / filename
    if path.is_file():
        path.unlink()
