"""Pipeline logic: prompt assembly, token budgeting, outline parsing, summaries.

Every generate_* function is blocking (called from a worker thread), streams
via on_chunk, and honors the cancel event. The exact prompts sent are kept in
LAST_PROMPTS for the "Show last prompt" viewer.
"""
from __future__ import annotations

import re
import threading
from typing import Callable, Optional

import applog
import backends
import prompts
from backends import SectionConfig
from project import (
    LorebookEntry,
    Scene,
    StoryProject,
    extract_style_guide,
    remove_style_guide,
)

CHARS_PER_TOKEN = 3.5
PREV_TAIL_TOKENS = 500

# section name -> {"system": ..., "user": ...} of the most recent call
LAST_PROMPTS: dict[str, dict] = {}

# set by the app to a thread-safe status-message emitter (str -> None)
NOTIFY = None


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def tail_text(text: str, max_tokens: int) -> str:
    """Last ~max_tokens of text, cut at a paragraph or sentence boundary."""
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    text = text.strip()
    if len(text) <= max_chars:
        return text
    chunk = text[-max_chars:]
    # prefer starting at a paragraph break, else a sentence start
    for pattern in ("\n\n", ". "):
        pos = chunk.find(pattern)
        if 0 <= pos < len(chunk) - 200:
            return chunk[pos + len(pattern):].strip()
    return chunk.strip()


def _record(section: str, system: str, user: str) -> None:
    LAST_PROMPTS[section] = {"system": system, "user": user}


def _run(
    cfg: SectionConfig,
    section: str,
    system: str,
    user: str,
    cancel: Optional[threading.Event],
    on_chunk: Optional[Callable[[str], None]],
) -> str:
    _record(section, system, user)
    applog.log(section, (
        f"request → {cfg.backend} {cfg.base_url} model={cfg.model or '(server default)'} "
        f"{cfg.params.describe()} prompt≈{estimate_tokens(system + user)}tok"))
    if applog.OPTIONS.get(f"prompts_{section}"):
        applog.log(section, f"SYSTEM PROMPT:\n{system}")
        applog.log(section, f"USER PROMPT:\n{user}")
    try:
        text = backends.stream_generate(cfg, system, user, cancel=cancel, on_chunk=on_chunk)
    except backends.BackendError as e:
        applog.log(section, f"ERROR: {e}")
        raise
    applog.log(section, f"done ← {len(text)} chars (≈{estimate_tokens(text)}tok)")
    if backends.LAST_FINISH_REASON == "length":
        warning = (f"⚠ The {section} output was CUT OFF by Max tokens "
                   f"({cfg.params.max_tokens}) — raise Max tokens for the "
                   "section that generates it (Story Start tab).")
        applog.log(section, warning)
        if NOTIFY is not None:
            NOTIFY(warning)
    if applog.OPTIONS.get("full_responses") and text.strip():
        applog.log(section, f"RESPONSE:\n{text.strip()}")
    elif (section == "summary" and applog.OPTIONS.get("summary_text")
            and text.strip()):
        applog.log(section, f"summary text: {text.strip()}")
    return text


# sections where a thinking model already needed a bigger budget this session;
# applied up front so every later call doesn't fail once and retry (which
# would double generation time for e.g. every summary in a batch)
_THINKING_FLOOR: dict[str, int] = {}


def _run_with_thinking_retry(
    cfg: SectionConfig,
    section: str,
    system: str,
    user: str,
    cancel: Optional[threading.Event],
    on_chunk: Optional[Callable[[str], None]],
) -> str:
    """Like _run, but if a thinking model spends its whole budget on hidden
    reasoning, retry with a bigger Max tokens (within the context) — and
    remember the bigger budget for this section for the rest of the session."""
    floor = _THINKING_FLOOR.get(section, 0)
    if floor > cfg.params.max_tokens:
        cfg = SectionConfig.from_dict(cfg.to_dict())
        cfg.params.max_tokens = floor
    try:
        return _run(cfg, section, system, user, cancel, on_chunk)
    except backends.ReasoningOnlyError as e:
        room = cfg.context_length - estimate_tokens(system + user) - 256
        new_max = max(cfg.params.max_tokens * 2,
                      e.reasoning_tokens * 2 + 1024, 2048)
        new_max = min(new_max, room)
        if new_max <= cfg.params.max_tokens:
            raise
        bigger = SectionConfig.from_dict(cfg.to_dict())
        bigger.params.max_tokens = int(new_max)
        applog.log(section, (
            f"thinking model used all {cfg.params.max_tokens} tokens on "
            f"reasoning — retrying with max_tokens={int(new_max)} (and using "
            "that budget for this section from now on)"))
        text = _run(bigger, section, system, user, cancel, on_chunk)
        _THINKING_FLOOR[section] = int(new_max)
        return text


# ---------------------------------------------------------------------------
# Outline parsing
# ---------------------------------------------------------------------------

_SCENE_SPLIT_RE = re.compile(r"^\s*(?:\*\*|#+\s*)?SCENE\s+(\d+)\s*:?\s*", re.IGNORECASE | re.MULTILINE)

# all field labels an outline block may contain (Default + Faithful-Detailed)
_OUTLINE_LABELS = ("LOCATION", "TIME", "CHARACTERS", "WHAT HAPPENS",
                   "KEY DETAILS", "CHARACTER FOCUS", "PURPOSE")
_LABELS_ALT = "|".join(_OUTLINE_LABELS)


def parse_outline(text: str) -> list[Scene]:
    """Parse 'SCENE n: ...' blocks into Scene objects."""
    matches = list(_SCENE_SPLIT_RE.finditer(text))
    scenes: list[Scene] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end():end].strip()
        lines = block.splitlines()
        title = lines[0].strip().strip("*# ") if lines else f"Scene {i + 1}"
        scene = Scene(title=title)
        body = "\n".join(lines[1:])

        def grab(label: str) -> str:
            pat = re.compile(
                rf"^\s*(?:\*\*|-\s*)?{label}\s*:?(?:\*\*)?\s*(.*?)(?=^\s*(?:\*\*|-\s*)?(?:{_LABELS_ALT})\s*:|\Z)",
                re.IGNORECASE | re.MULTILINE | re.DOTALL,
            )
            mm = pat.search(body)
            return mm.group(1).strip() if mm else ""

        scene.location = grab("LOCATION")
        when = grab("TIME")
        if when:
            scene.location = f"{scene.location} — {when}" if scene.location else when
        scene.characters = grab("CHARACTERS")
        scene.beat = grab("WHAT HAPPENS")
        details = grab("KEY DETAILS")
        if details:
            scene.beat += f"\n\nKey details: {details}"
        focus = grab("CHARACTER FOCUS")
        if focus:
            scene.beat += f"\n\nCharacter focus: {focus}"
        scene.purpose = grab("PURPOSE")
        if not scene.beat.strip():
            scene.beat = block
        scenes.append(scene)
    return scenes


# ---------------------------------------------------------------------------
# Lorebook matching
# ---------------------------------------------------------------------------

def match_lorebook(entries: list[LorebookEntry], *scan_texts: str) -> str:
    """Entries whose keywords appear in any scan text (plus always-on ones)."""
    haystack = "\n".join(t for t in scan_texts if t).lower()
    picked: list[LorebookEntry] = []
    for entry in entries:
        if entry.always_include:
            picked.append(entry)
            continue
        keys = [k.strip().lower() for k in ([entry.name] + entry.keywords) if k.strip()]
        if any(k in haystack for k in keys):
            picked.append(entry)
    if not picked:
        return "(none)"
    return "\n".join(f"- {e.name}: {e.content}" for e in picked)


# ---------------------------------------------------------------------------
# Generation stages
# ---------------------------------------------------------------------------

def _wants_language(language: str) -> bool:
    return bool(language) and language.strip().lower() not in ("", "english")


def generate_storyboard(
    cfg: SectionConfig,
    concept: str,
    num_scenes: int,
    language: str = "English",
    plan_in_language: bool = False,
    cancel: Optional[threading.Event] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> str:
    tpl = prompts.get_prompt(cfg.prompt_preset, "storyboard")
    values = {"concept": concept.strip(), "num_scenes": num_scenes}
    system = prompts.fill(tpl["system"], values)
    user = prompts.fill(tpl["user"], values)
    if plan_in_language and _wants_language(language):
        system += f"\n\nWrite the entire story board in {language}."
    return _run_with_thinking_retry(cfg, "storyboard", system, user,
                                    cancel, on_chunk).strip()


def generate_outline(
    cfg: SectionConfig,
    storyboard: str,
    num_scenes: int,
    language: str = "English",
    plan_in_language: bool = False,
    cancel: Optional[threading.Event] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> tuple[str, list[Scene]]:
    tpl = prompts.get_prompt(cfg.prompt_preset, "outline")
    values = {"storyboard": storyboard.strip(), "num_scenes": num_scenes}
    system = prompts.fill(tpl["system"], values)
    user = prompts.fill(tpl["user"], values)
    if plan_in_language and _wants_language(language):
        system += (
            f"\n\nWrite the scene descriptions in {language}, but keep the "
            "SCENE/LOCATION/CHARACTERS/WHAT HAPPENS/PURPOSE labels in English "
            "exactly as shown."
        )
    text = _run_with_thinking_retry(cfg, "outline", system, user,
                                    cancel, on_chunk).strip()
    return text, parse_outline(text)


CONTEXT_SUMMARIES = "summaries"
CONTEXT_FULL = "full"


def build_scene_prompts(
    cfg: SectionConfig,
    project: StoryProject,
    index: int,
    lorebook_entries: list[LorebookEntry],
    continuation: str = "",
    context_mode: str = CONTEXT_SUMMARIES,
) -> tuple[str, str]:
    """Assemble (system, user) for scene `index` (0-based) within budget."""
    scene = project.scenes[index]
    scene_number = index + 1
    num_scenes = len(project.scenes)

    prev_tail = ""
    if index > 0 and project.scenes[index - 1].text.strip():
        prev_tail = tail_text(project.scenes[index - 1].text, PREV_TAIL_TOKENS)

    # ALL previous scenes go into the context: scenes 1..k-1 as summaries (or
    # full text in full mode). The previous scene additionally contributes its
    # verbatim ending so the prose continues seamlessly.
    summary_parts = []
    for i in range(0, index):
        s = project.scenes[i]
        if context_mode == CONTEXT_FULL and s.text.strip():
            summary_parts.append(f"Scene {i + 1} ({s.title}):\n{s.text.strip()}")
        elif s.summary.strip():
            summary_parts.append(f"Scene {i + 1} ({s.title}): {s.summary.strip()}")
    summaries = "\n\n".join(summary_parts) if summary_parts else "(this is the first scene)"
    if context_mode == CONTEXT_FULL and index > 0:
        # the previous scene's full text is already above — no separate tail
        prev_tail = ""

    lorebook = match_lorebook(lorebook_entries, scene.outline_block(scene_number), prev_tail)
    style_guide = extract_style_guide(project.storyboard_text) or "(follow the storyboard)"

    tpl = prompts.get_prompt(cfg.prompt_preset, "scene")
    # avoid sending the style guide twice: if the template has a separate
    # {style_guide} slot, strip that section out of the storyboard text
    board_text = project.storyboard_text.strip()
    if "{style_guide}" in (tpl["system"] + tpl["user"]) and style_guide != "(follow the storyboard)":
        stripped = remove_style_guide(project.storyboard_text)
        if stripped:
            board_text = stripped
    empty_tail_note = (
        "(the previous scene appears in full above — continue seamlessly from "
        "its final sentence)"
        if context_mode == CONTEXT_FULL and index > 0
        else "(this is the first scene — start the story)")

    def build(prev_tail_now: str, summaries_now: str) -> tuple[str, str]:
        values = {
            "storyboard": board_text,
            "style_guide": style_guide,
            "lorebook": lorebook,
            "summaries": summaries_now,
            "previous_tail": prev_tail_now or empty_tail_note,
            "scene_beat": scene.outline_block(scene_number),
            "scene_number": scene_number,
            "scene_title": scene.title,
            "num_scenes": num_scenes,
            "target_length": project.target_length,
        }
        system = prompts.fill(tpl["system"], values)
        user = prompts.fill(tpl["user"], values)
        if continuation:
            user += (
                "\n\nThe scene was started but is unfinished. Here is what exists so "
                "far — continue it seamlessly from the exact point it stops, without "
                "repeating anything:\n\n" + tail_text(continuation, PREV_TAIL_TOKENS)
            )
        return system, user

    budget = cfg.context_length - cfg.params.max_tokens - 256
    system, user = build(prev_tail, summaries)

    # Over budget: shrink previous tail, then drop it, then trim old summaries.
    if estimate_tokens(system + user) > budget and prev_tail:
        system, user = build(tail_text(prev_tail, 200), summaries)
    if estimate_tokens(system + user) > budget and prev_tail:
        system, user = build("", summaries)
    while estimate_tokens(system + user) > budget and len(summary_parts) > 1:
        # merge the two oldest summaries into a heavily shortened line
        first = summary_parts.pop(0)
        summary_parts[0] = ("Earlier: " + first.split(":", 1)[-1].strip())[:400] + \
            "\n\n" + summary_parts[0]
        summaries = "\n\n".join(summary_parts)
        system, user = build("", summaries)
    if estimate_tokens(system + user) > budget and summary_parts:
        # last resort (mostly full-scenes mode): keep only the newest part
        summaries = tail_text(summaries, max(300, budget // 3))
        system, user = build("", summaries)

    return system, user


def context_report(
    cfg: SectionConfig,
    project: StoryProject,
    index: int,
    lorebook_entries: list[LorebookEntry],
    context_mode: str = CONTEXT_SUMMARIES,
) -> tuple[str, str]:
    """(short verdict line, detailed breakdown) for scene `index`'s budget."""
    if not (0 <= index < len(project.scenes)):
        return "", ""
    scene = project.scenes[index]
    board_tok = estimate_tokens(remove_style_guide(project.storyboard_text)
                                or project.storyboard_text)
    style_tok = estimate_tokens(extract_style_guide(project.storyboard_text))
    n_prev = 0
    prev_ctx_tok = 0
    for i in range(0, index):
        s = project.scenes[i]
        source = s.text if context_mode == CONTEXT_FULL else s.summary
        if source.strip():
            n_prev += 1
            prev_ctx_tok += estimate_tokens(source)
    tail_tok = 0
    if index > 0 and project.scenes[index - 1].text.strip() \
            and context_mode != CONTEXT_FULL:
        tail_tok = min(PREV_TAIL_TOKENS,
                       estimate_tokens(project.scenes[index - 1].text))
    lore_tok = estimate_tokens(match_lorebook(
        lorebook_entries, scene.outline_block(index + 1)))
    beat_tok = estimate_tokens(scene.outline_block(index + 1))
    overhead = 350  # prompt template text
    reserve = cfg.params.max_tokens
    prompt_tok = board_tok + style_tok + prev_ctx_tok + tail_tok + lore_tok \
        + beat_tok + overhead
    total = prompt_tok + reserve
    ctx = cfg.context_length
    prompt_total = total - reserve
    ok = total <= ctx - 256
    short = f"Token use for this scene: ≈{total} of {ctx} — "
    if ok:
        short += "OK ✓"
    else:
        short += "TOO BIG ⚠ (older story context gets trimmed automatically)"
        if reserve > ctx // 3:
            short += " — lowering the Scene Writer's Max tokens would fix it"
    short += "   (hover for the breakdown)"

    prev_label = ("Full text of" if context_mode == CONTEXT_FULL
                  else "Summaries of")
    detail_lines = [
        f"What gets sent for scene {index + 1}:",
        f"    Storyboard: ≈{board_tok}",
        f"    Style guide: ≈{style_tok}",
        f"    {prev_label} {n_prev} earlier scene(s): ≈{prev_ctx_tok}",
        f"    End of previous scene: ≈{tail_tok}",
        f"    Lorebook facts: ≈{lore_tok}",
        f"    This scene's outline: ≈{beat_tok}",
        f"    Prompt instructions: ≈{overhead}",
        f"    = prompt ≈{prompt_total}",
        f"    + space reserved for the reply (Scene Writer Max tokens): {reserve}",
        f"    = ≈{total} of the server's {ctx}-token context",
    ]
    if not ok:
        detail_lines.append(
            "⚠ Over budget: the oldest summaries / previous-scene ending are "
            "trimmed automatically so it still fits.")
        if reserve > ctx // 3:
            detail_lines.append(
                f"Tip: Max tokens ({reserve}) is the biggest cost here — a "
                f"{project.target_length}-word scene only needs "
                f"≈{int(project.target_length * 1.8)} tokens of reply space.")
        detail_lines.append(
            "To get a truly bigger context you must raise it in BOTH places: "
            "start the server with more (koboldcpp --contextsize 16384, "
            "llama-server -c 16384, Ollama OLLAMA_CONTEXT_LENGTH=16384) AND "
            "set the same number in the LLM Settings tab.")
    return short, "\n".join(detail_lines)


def generate_scene(
    cfg: SectionConfig,
    project: StoryProject,
    index: int,
    lorebook_entries: list[LorebookEntry],
    continuation: str = "",
    context_mode: str = CONTEXT_SUMMARIES,
    language: str = "English",
    cancel: Optional[threading.Event] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> str:
    system, user = build_scene_prompts(cfg, project, index, lorebook_entries,
                                       continuation, context_mode)
    if _wants_language(language):
        system += (
            f"\n\nIMPORTANT: Write all story prose in {language}. Only the "
            "language of the prose changes — still follow every other rule "
            "and the style guide."
        )
    return _run_with_thinking_retry(cfg, "scene", system, user,
                                    cancel, on_chunk).strip()


def generate_summary(
    cfg: SectionConfig,
    scene_text: str,
    language: str = "English",
    cancel: Optional[threading.Event] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> str:
    tpl = prompts.get_prompt(cfg.prompt_preset, "summary")
    values = {"scene_text": scene_text.strip()}
    system = prompts.fill(tpl["system"], values)
    user = prompts.fill(tpl["user"], values)
    if _wants_language(language):
        user += f"\n\nWrite the summary in {language}."
    try:
        return _run_with_thinking_retry(cfg, "summary", system, user,
                                        cancel, on_chunk).strip()
    except backends.ReasoningOnlyError:
        # a missing summary must never kill a story/batch — fall back to an
        # excerpt of the scene so later scenes still get some continuity
        applog.log("summary", (
            "summarizer produced only reasoning even after retry — using a "
            "scene excerpt as the summary instead"))
        return ("(automatic excerpt — the summarizer model produced no "
                "summary) …" + tail_text(scene_text, 150))


def generate_character_cards(
    cfg: SectionConfig,
    storyboard: str,
    cancel: Optional[threading.Event] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> list[LorebookEntry]:
    system = (
        "You extract character reference cards from a story plan. You are precise "
        "and only state facts present in the plan."
    )
    user = (
        "From the following storyboard, list every named character, one per line, "
        "in exactly this format:\n"
        "Name: 2-3 sentences of facts (role, appearance, personality, motivation)\n\n"
        "Output only these lines, nothing else.\n\n"
        "STORYBOARD:\n" + storyboard.strip()
    )
    text = _run(cfg, "characters", system, user, cancel, on_chunk)
    return parse_character_entries(text)


def parse_character_entries(text: str) -> list[LorebookEntry]:
    """Parse 'Name: description' lines from an import-characters LLM response."""
    entries = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line or ":" not in line:
            continue
        name, _, content = line.partition(":")
        name = name.strip().strip("*_")
        content = content.strip()
        if name and content and len(name) < 60:
            entries.append(LorebookEntry(name=name, keywords=[name], content=content))
    return entries
