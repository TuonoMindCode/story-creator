"""Prompt templates for every pipeline section, with switchable presets.

Sections: storyboard, outline, scene, summary.
Presets:  Default, Strict, Qwen-tuned, Gemma-tuned (+ user presets on disk).

Templates use named placeholders filled by pipeline.py:
  {concept}        story concept text (free text or description file)
  {storyboard}     full storyboard text
  {style_guide}    narrative style guide section of the storyboard
  {scene_beats}    the full numbered scene outline
  {summaries}      compact summaries of earlier scenes
  {previous_tail}  verbatim ending of the previous scene
  {scene_beat}     the outline entry for the scene being written
  {scene_number}   number of the scene being written
  {scene_title}    title of the scene being written
  {num_scenes}     total number of scenes
  {target_length}  target word count for a scene
  {lorebook}       matched lorebook entries (world & character facts)
  {scene_text}     full text of a scene (summary calls)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SECTIONS = ["storyboard", "outline", "scene", "summary"]

PLACEHOLDERS = {
    "concept": "Story concept text from tab 1 (free text or description file)",
    "storyboard": "Full storyboard text",
    "style_guide": "Narrative style guide section of the storyboard",
    "scene_beats": "The full numbered scene outline",
    "summaries": "Compact summaries of earlier scenes",
    "previous_tail": "Verbatim ending of the previous scene",
    "scene_beat": "The outline entry for the scene being written",
    "scene_number": "Number of the scene being written",
    "scene_title": "Title of the scene being written",
    "num_scenes": "Total number of scenes",
    "target_length": "Target word count for a scene",
    "lorebook": "Matched lorebook entries (world & character facts)",
    "scene_text": "Full text of a scene (used by summary calls)",
}

PRESET_DIR = Path(__file__).parent / "prompt-presets"

# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

_DEFAULT = {
    "storyboard": {
        "system": (
            "You are an experienced story developer and fiction editor. You turn story "
            "ideas into detailed, concrete story plans that a novelist can write from. "
            "You invent specific names, places and details rather than staying vague."
        ),
        "user": (
            "Create a complete story board for the following story concept.\n\n"
            "STORY CONCEPT:\n{concept}\n\n"
            "The story will be told in {num_scenes} scenes.\n\n"
            "Write the story board using exactly these markdown sections:\n\n"
            "# Title\n"
            "# Logline\n"
            "(1-2 sentences that capture the whole story)\n"
            "# Genre & Tone\n"
            "# Setting\n"
            "(where and when, with concrete detail)\n"
            "# Main Characters\n"
            "(for each character: **Name** — role, appearance, personality, motivation, "
            "and a secret or flaw. Give every character a clearly distinct "
            "name: no two unrelated characters may share a surname or a "
            "variant of one — Petrov and Petrova, Volkov and Volkova count as "
            "the same name — and avoid first names that begin with the same "
            "letter.)\n"
            "# Plot Summary\n"
            "(three paragraphs labeled Beginning, Middle, End)\n"
            "# Reveals\n"
            "(only if the story hides something from the reader — an identity, "
            "a culprit, a twist. One line each, in the form 'Scene 4: the "
            "victim is Elara Petrova'. Anything listed here is hidden from the "
            "writer until that scene, so ALSO make sure the sections above "
            "refer to the secret only by what the characters know at the start "
            "— for example call her Jane Doe in Main Characters and Plot "
            "Summary, never by the name that is revealed later. Omit this "
            "section entirely if nothing is withheld.)\n"
            "# Themes\n"
            "# Narrative Style Guide\n"
            "(point of view, tense, narrative voice, pacing, dialogue style — written as "
            "concrete instructions the writer must follow)\n\n"
            "If the story concept lists writing style requirements (dialogue amount, "
            "pacing, detail, prose style, humor, violence, romance, ending), carry ALL "
            "of them into the Narrative Style Guide as concrete instructions.\n"
            "Be specific and concrete. Do not write the story itself."
        ),
    },
    "outline": {
        "system": (
            "You are a professional story outliner. You break a story plan into scenes "
            "that flow into each other, each with its own purpose and conflict. You "
            "follow the requested output format exactly."
        ),
        "user": (
            "Break the following story into exactly {num_scenes} scenes.\n\n"
            "STORY BOARD:\n{storyboard}\n\n"
            "For every scene use exactly this format:\n\n"
            "SCENE 1: <scene title>\n"
            "LOCATION: <where it takes place>\n"
            "CHARACTERS: <who appears>\n"
            "WHAT HAPPENS: <2-4 sentences describing the events of the scene>\n"
            "PURPOSE: <the scene's goal or conflict, and what changes by the end>\n\n"
            "Rules:\n"
            "- Output exactly {num_scenes} scenes, numbered SCENE 1 through SCENE {num_scenes}.\n"
            "- The scenes together must cover the whole plot, beginning to end.\n"
            "- Every scene must move the story forward; no filler scenes.\n"
            "- Scenes must not overlap. Each one covers different events at a "
            "later point in time than the one before it. Never split a single "
            "conversation, meeting or event across two scenes, and never let a "
            "scene revisit what an earlier scene already covered.\n"
            "- Output only the scene list, nothing else."
        ),
    },
    "scene": {
        "system": (
            "You are a skilled novelist writing one scene of a longer story. Stay true "
            "to the story board and follow the style guide exactly.\n\n"
            "STORY BOARD (condensed):\n{storyboard}\n\n"
            "STYLE GUIDE:\n{style_guide}\n\n"
            "WORLD & CHARACTER FACTS:\n{lorebook}\n\n"
            "Rules:\n"
            "- Write ONLY scene {scene_number} of {num_scenes}. Do not write past it.\n"
            "- Target length: about {target_length} words of prose.\n"
            "- Show, don't tell. Use dialogue, action and sensory detail.\n"
            "- Do not summarize events, do not skip ahead, and do not end the whole "
            "story unless this is the final scene.\n"
            "- End the scene at a natural stopping point.\n"
            "- Output only story prose — no headings, no scene numbers, no notes.\n"
            "- Names, roles, employers and gender of every character are fixed "
            "by the story board and the earlier scenes. Never rename someone, "
            "never invent a second name for a character who already has one, "
            "and never use placeholder names. If a character is not named in "
            "the plan, give them one name and keep it.\n"
            "- Any character you introduce yourself must get a name that is "
            "clearly distinct from everyone already in the story — never give "
            "an unrelated character the same surname as another.\n"
            "- Never leave anything unwritten. If you do not know a detail, "
            "invent one that fits; never write a bracketed stand-in such as "
            "[some letters], [insert name] or [description].\n"
            "- Write only the story. Never address the reader, never comment on "
            "the act of writing, and never explain your own choices inside the "
            "prose (no asides such as 'let us call him X').\n"
            "- Never paste documents into the story. Job adverts, emails, "
            "letters, contracts, forms, screens and lists must be rendered the "
            "way novels do it: through the character reading them — the one "
            "phrase that stings quoted, the rest carried by her reaction and "
            "what she thinks about it. No markdown, no bullet lists, no bold "
            "or italic labels, no field names like 'Subject:' or 'Salary:'."
        ),
        "user": (
            "BACKGROUND — THE STORY SO FAR (scene summaries):\n{summaries}\n\n"
            "BACKGROUND — THE PREVIOUS SCENE (reference only, already written "
            "and already read by the reader; use it for continuity of voice, "
            "place, mood and where the characters now stand):\n"
            "{previous_tail}\n\n"
            "==========================================\n"
            "YOUR TASK - WRITE SCENE {scene_number}: {scene_title}\n"
            "==========================================\n\n"
            "WHAT HAPPENS IN THIS SCENE (this governs the whole scene):\n"
            "{scene_beat}\n\n"
            "Use the previous scene for continuity of facts and voice — but "
            "do not drift into copying its shape by habit: unless the style "
            "guide or this scene's outline actually calls for a recurring "
            "pattern, give this scene its own opening move and its own way of "
            "ending, rather than reusing the previous scene's closing device "
            "or rhetorical formula.\n\n"
            "How to start: give at most the opening paragraph to the "
            "connection — the characters carrying the immediate aftermath of "
            "the previous scene with them (where they are now, what they are "
            "still feeling or dealing with) — then move into this scene's own "
            "events and stay there. Begin AFTER the previous scene's final "
            "line: never retell, replay or re-quote its events or dialogue, "
            "and never rewrite it from another angle. Do not open by finishing "
            "the physical action the previous scene ended on (rising from the "
            "chair they sat down in, reopening the laptop they closed); open "
            "at this scene's own moment, and do not start it with the same "
            "subject and verb the previous scene started with.\n\n"
            "Write the full prose of scene {scene_number} now, delivering the "
            "events described above."
        ),
    },
    "summary": {
        "system": (
            "You summarize scenes of a story for a writer's continuity notes. You are "
            "precise about facts: names, places, injuries, objects, discoveries, "
            "relationships and time of day."
        ),
        "user": (
            "Summarize the following scene in about 120 words. Include the key events, "
            "any character or relationship developments, and every concrete fact that "
            "must stay consistent later in the story. Output only the summary.\n\n"
            "SCENE TEXT:\n{scene_text}"
        ),
    },
}

# Strict: stronger, numbered, harder-edged instructions for models that drift.
_STRICT = {
    "storyboard": {
        "system": _DEFAULT["storyboard"]["system"]
        + " You NEVER deviate from the requested output format.",
        "user": _DEFAULT["storyboard"]["user"]
        + (
            "\n\nSTRICT REQUIREMENTS:\n"
            "1. Use every section header listed above, in that exact order.\n"
            "2. Do NOT add extra sections, greetings, or commentary.\n"
            "3. Do NOT write any story prose.\n"
            "4. Every character MUST have a stated motivation and a flaw."
        ),
    },
    "outline": {
        "system": _DEFAULT["outline"]["system"],
        "user": _DEFAULT["outline"]["user"]
        + (
            "\n\nSTRICT REQUIREMENTS:\n"
            "1. Any output that is not in the SCENE/LOCATION/CHARACTERS/WHAT HAPPENS/"
            "PURPOSE format is WRONG.\n"
            "2. Do not merge scenes. Do not add commentary before or after the list."
        ),
    },
    "scene": {
        "system": _DEFAULT["scene"]["system"]
        + (
            "\n\nSTRICT REQUIREMENTS:\n"
            "1. Writing beyond the events of this scene is WRONG.\n"
            "2. Output that contains headings, numbering or notes is WRONG.\n"
            "3. You MUST stay in the point of view and tense from the style guide.\n"
            "4. Contradicting any fact in the summaries or the world facts is WRONG."
        ),
        "user": _DEFAULT["scene"]["user"],
    },
    "summary": {
        "system": _DEFAULT["summary"]["system"],
        "user": _DEFAULT["summary"]["user"]
        + "\n\nOutput MUST be a single paragraph. No headings, no bullet points.",
    },
}

# Qwen-tuned: Qwen follows structure well; suppress reasoning-style output.
_QWEN = {
    sec: {
        "system": tpl["system"]
        + " Output only the requested content — no reasoning, no <think> blocks, "
        "no explanations of what you are doing.",
        "user": tpl["user"],
    }
    for sec, tpl in _DEFAULT.items()
}

# Gemma-tuned: Gemma merges system into the user turn and responds best to
# short, direct instructions placed close to the task.
_GEMMA = {
    "storyboard": {
        "system": "You are a story developer. Be concrete and specific.",
        "user": _DEFAULT["storyboard"]["user"]
        + "\n\nImportant: respond with the story board only, starting directly with '# Title'.",
    },
    "outline": {
        "system": "You are a story outliner. Follow the output format exactly.",
        "user": _DEFAULT["outline"]["user"]
        + "\n\nImportant: start your response directly with 'SCENE 1:'.",
    },
    "scene": {
        "system": (
            "You are a novelist. Write vivid prose with dialogue and sensory detail.\n\n"
            "STORY BOARD (condensed):\n{storyboard}\n\n"
            "STYLE GUIDE:\n{style_guide}\n\n"
            "WORLD & CHARACTER FACTS:\n{lorebook}"
        ),
        "user": _DEFAULT["scene"]["user"]
        + (
            "\n\nImportant: write only scene {scene_number} of {num_scenes}, about "
            "{target_length} words, prose only, and stop at a natural scene ending. "
            "Do not paste documents, emails or lists as formatted blocks — show "
            "them through the character reading them, in plain prose."
        ),
    },
    "summary": {
        "system": "You write precise continuity notes for a story writer.",
        "user": _DEFAULT["summary"]["user"],
    },
}

# A richer summary template — pick this preset for the Summarizer section when
# the default ~120-word summaries drop too much detail. Its other sections fall
# back to Default automatically.
_DETAILED_SUMMARY = {
    "summary": {
        "system": (
            "You write detailed continuity notes about story scenes for the "
            "writer of the following scenes. You are precise about facts and "
            "never invent details that are not in the scene."
        ),
        "user": (
            "Summarize the following scene in about 250 words for continuity "
            "notes. Cover, in this order:\n"
            "1. The events of the scene, in order.\n"
            "2. What each character does and feels; any character development.\n"
            "3. New information revealed (clues, secrets, discoveries).\n"
            "4. People, places and objects introduced — with their exact names.\n"
            "5. Changes in relationships between characters.\n"
            "6. When it happens (time of day, how much time has passed).\n"
            "7. Unresolved threads or hooks the scene leaves open.\n\n"
            "Include every concrete fact that must stay consistent later "
            "(names, injuries, items, promises, locations). Write flowing "
            "prose, no numbered headings. Output only the summary.\n\n"
            "SCENE TEXT:\n{scene_text}"
        ),
    },
}

# Faithful-Detailed: binds the storyboard tightly to the user's story
# description and produces much richer scene outlines — pick it for the
# Storyboard and/or Scene Planner sections. Scene/summary fall back to Default.
_FAITHFUL = {
    "storyboard": {
        "system": (
            "You are an experienced story developer and fiction editor. You treat "
            "the user's story concept as a binding specification: every stated "
            "detail — characters, settings, plot points, style requirements — "
            "MUST appear in your story plan, expanded but never replaced or "
            "dropped. You invent additional specific names, places and details "
            "to fill the gaps."
        ),
        "user": (
            "Create a complete story board for the following story concept.\n\n"
            "STORY CONCEPT (binding requirements — nothing may be dropped):\n"
            "{concept}\n\n"
            "The story will be told in {num_scenes} scenes.\n\n"
            "Write the story board using exactly these markdown sections:\n\n"
            "# Title\n"
            "# Logline\n"
            "(1-2 sentences that capture the whole story)\n"
            "# Genre & Tone\n"
            "(exactly as the concept specifies, refined, never contradicted)\n"
            "# Setting\n"
            "(where and when, with concrete detail; use the concept's setting)\n"
            "# Main Characters\n"
            "(for each character: **Name** — role, appearance, personality, "
            "motivation, and a secret or flaw. Characters named in the concept "
            "MUST appear here with everything the concept says about them. "
            "Give every character a clearly distinct name: no two unrelated "
            "characters may share a surname, and avoid first names that begin "
            "with the same letter.)\n"
            "# Plot Summary\n"
            "(three paragraphs labeled Beginning, Middle, End. Every plot point "
            "from the concept must be woven in.)\n"
            "# Reveals\n"
            "(only if the story hides something from the reader — an identity, "
            "a culprit, a twist. One line each, in the form 'Scene 4: the "
            "victim is Elara Petrova'. Anything listed here is hidden from the "
            "writer until that scene, so ALSO make sure the sections above "
            "refer to the secret only by what the characters know at the start "
            "— for example call her Jane Doe in Main Characters and Plot "
            "Summary, never by the name that is revealed later. Omit this "
            "section entirely if nothing is withheld.)\n"
            "# Themes\n"
            "# Narrative Style Guide\n"
            "(point of view, tense, narrative voice, pacing, dialogue style — "
            "concrete instructions. Copy EVERY writing style requirement from "
            "the concept into this guide.)\n"
            "# Concept Checklist\n"
            "(list each requirement from the story concept as one short line, "
            "each followed by where it appears in this story board — this is "
            "your proof that nothing was dropped)\n\n"
            "Be specific and concrete. Do not write the story itself."
        ),
    },
    "outline": {
        "system": (
            "You are a professional story outliner. You break a story plan into "
            "scenes with enough concrete detail that a novelist can write over "
            "a thousand words from each scene description without inventing "
            "major events. You follow the requested output format exactly and "
            "stay strictly true to the story board."
        ),
        "user": (
            "Break the following story into exactly {num_scenes} scenes.\n\n"
            "STORY BOARD:\n{storyboard}\n\n"
            "For every scene use exactly this format:\n\n"
            "SCENE 1: <scene title>\n"
            "LOCATION: <where it takes place, concretely>\n"
            "TIME: <time of day + how much time has passed since the previous scene>\n"
            "CHARACTERS: <who appears>\n"
            "WHAT HAPPENS: <6-10 sentences describing the events step by step: "
            "how the scene opens, what each character does, the key things that "
            "are said (as points, not full dialogue), what is discovered or "
            "revealed, the complication, and how the scene ends>\n"
            "KEY DETAILS: <concrete specifics the writer must include: named "
            "objects, clues, sensory details, small facts that matter later>\n"
            "CHARACTER FOCUS: <what the viewpoint character feels, thinks and "
            "wants in this scene; any change in them>\n"
            "PURPOSE: <the scene's goal or conflict, and what has changed by "
            "the end>\n\n"
            "Rules:\n"
            "- Output exactly {num_scenes} scenes, numbered SCENE 1 through "
            "SCENE {num_scenes}.\n"
            "- The scenes together must cover the whole plot of the story "
            "board, beginning to end — every plot point placed in a scene.\n"
            "- Every scene must move the story forward; no filler scenes.\n"
            "- Scenes must not overlap. Each one covers different events at a "
            "later point in time than the one before it. Never split a single "
            "conversation, meeting or event across two scenes, and never let a "
            "scene revisit what an earlier scene already covered.\n"
            "- Vague beats like 'the detective investigates' are NOT acceptable "
            "— state what is investigated, what is found, and what it means.\n"
            "- Output only the scene list, nothing else."
        ),
    },
}

BUILTIN_PRESETS: dict[str, dict] = {
    "Default": _DEFAULT,
    "Strict": _STRICT,
    "Qwen-tuned": _QWEN,
    "Gemma-tuned": _GEMMA,
    "Detailed-Summary": _DETAILED_SUMMARY,
    "Faithful-Detailed": _FAITHFUL,
}

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fill(template: str, values: dict) -> str:
    """Fill placeholders; unknown placeholders are left as-is."""
    return template.format_map(_SafeDict(values))


def find_unknown_placeholders(template: str) -> list[str]:
    return sorted({m for m in _PLACEHOLDER_RE.findall(template) if m not in PLACEHOLDERS})


# ---------------------------------------------------------------------------
# User presets on disk: prompt-presets/<name>.json holding all four sections
# ---------------------------------------------------------------------------

def _preset_path(name: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_\- ]", "", name).strip() or "preset"
    return PRESET_DIR / f"{safe}.json"


def load_user_presets() -> dict[str, dict]:
    presets = {}
    if PRESET_DIR.is_dir():
        for f in sorted(PRESET_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    presets[f.stem] = data
            except (OSError, json.JSONDecodeError):
                continue
    return presets


def save_user_preset(name: str, preset: dict) -> Path:
    PRESET_DIR.mkdir(exist_ok=True)
    path = _preset_path(name)
    path.write_text(json.dumps(preset, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def all_presets() -> dict[str, dict]:
    """Built-in presets plus user presets (user presets may shadow built-ins)."""
    presets = {k: v for k, v in BUILTIN_PRESETS.items()}
    presets.update(load_user_presets())
    return presets


def get_prompt(preset_name: str, section: str) -> dict:
    """Returns {'system': ..., 'user': ...} with Default as fallback."""
    presets = all_presets()
    preset = presets.get(preset_name, BUILTIN_PRESETS["Default"])
    tpl = preset.get(section) or BUILTIN_PRESETS["Default"][section]
    return {"system": tpl.get("system", ""), "user": tpl.get("user", "")}
