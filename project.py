"""Data model + persistence: storyboards, lorebooks, story projects, export."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP_DIR = Path(__file__).parent
DESCRIPTIONS_DIR = APP_DIR / "story-descriptions"
STORYBOARDS_DIR = APP_DIR / "storyboards"
PROJECTS_DIR = APP_DIR / "projects"
OUTPUT_DIR = APP_DIR / "output"
SETTINGS_FILE = APP_DIR / "settings.json"

SCENE_OUTLINED = "outlined"
SCENE_WRITING = "writing"
SCENE_WRITTEN = "written"
SCENE_EDITED = "edited"


def slugify(name: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_\- ]", "", name).strip().replace(" ", "-")
    slug = re.sub(r"-+", "-", slug)
    return (slug[:max_len] or "untitled").strip("-")


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    directory.mkdir(exist_ok=True)
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


# ---------------------------------------------------------------------------
# Lorebook
# ---------------------------------------------------------------------------

# pronoun options offered for characters; free text is allowed too
PRONOUN_CHOICES = ("", "she/her", "he/him", "they/them")


@dataclass
class LorebookEntry:
    name: str = ""
    keywords: list[str] = field(default_factory=list)
    content: str = ""
    always_include: bool = False
    pronouns: str = ""   # kept separate so it can never be lost in the prose

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LorebookEntry":
        return cls(
            name=d.get("name", ""),
            keywords=list(d.get("keywords", [])),
            content=d.get("content", ""),
            always_include=bool(d.get("always_include", False)),
            pronouns=d.get("pronouns", ""),
        )

    def as_fact_line(self) -> str:
        who = f"{self.name} ({self.pronouns})" if self.pronouns else self.name
        return f"- {who}: {self.content}"


def lorebook_path(storyboard_name: str) -> Path:
    return STORYBOARDS_DIR / f"{storyboard_name}.lorebook.json"


def load_lorebook(storyboard_name: str) -> list[LorebookEntry]:
    path = lorebook_path(storyboard_name)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [LorebookEntry.from_dict(e) for e in data]
    except (OSError, json.JSONDecodeError):
        return []


def save_lorebook(storyboard_name: str, entries: list[LorebookEntry]) -> None:
    STORYBOARDS_DIR.mkdir(exist_ok=True)
    lorebook_path(storyboard_name).write_text(
        json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Storyboard library (plain markdown files in storyboards/)
# ---------------------------------------------------------------------------

def list_storyboards() -> list[str]:
    if not STORYBOARDS_DIR.is_dir():
        return []
    return sorted(
        f.stem for f in STORYBOARDS_DIR.glob("*.md")
    )


def load_storyboard(name: str) -> str:
    path = STORYBOARDS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def save_storyboard(name: str, text: str) -> None:
    STORYBOARDS_DIR.mkdir(exist_ok=True)
    (STORYBOARDS_DIR / f"{name}.md").write_text(text, encoding="utf-8")


def delete_storyboard(name: str) -> None:
    for path in (STORYBOARDS_DIR / f"{name}.md", lorebook_path(name)):
        if path.exists():
            path.unlink()


def rename_storyboard(old: str, new: str) -> str:
    new = slugify(new)
    if not new or new == old:
        return old
    STORYBOARDS_DIR.mkdir(exist_ok=True)
    target = unique_path(STORYBOARDS_DIR, new, ".md")
    new = target.stem
    (STORYBOARDS_DIR / f"{old}.md").rename(target)
    old_lb = lorebook_path(old)
    if old_lb.exists():
        old_lb.rename(lorebook_path(new))
    return new


def extract_storyboard_title(text: str) -> str:
    m = re.search(r"^#\s*Title\s*\n+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip().lstrip("#").strip()
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def extract_style_guide(text: str) -> str:
    m = re.search(
        r"^#+\s*Narrative Style Guide\s*\n(.*?)(?=^#\s|\Z)", text,
        re.MULTILINE | re.DOTALL,
    )
    return m.group(1).strip() if m else ""


_CHAR_LINE_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?\*{0,2}([A-Z][^\n*:—–-]{1,60}?)\*{0,2}\s*[—–:-]\s*(.+)$")


def extract_characters(text: str) -> list[tuple[str, str]]:
    """(name, description) pairs from the storyboard's Main Characters section.

    Used to keep names/roles fixed while writing when no lorebook exists.
    """
    m = re.search(r"^#+\s*(?:Main\s+)?Characters\s*\n(.*?)(?=^#\s|\Z)", text,
                  re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    out: list[tuple[str, str]] = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("("):
            continue
        hit = _CHAR_LINE_RE.match(line)
        if not hit:
            continue
        name = hit.group(1).strip().strip("*_ ")
        desc = hit.group(2).strip()
        if name and desc and len(name.split()) <= 5:
            out.append((name, desc))
    return out


def remove_character_lines(text: str, names) -> str:
    """The story board with the given characters' entries taken out.

    The board lists the whole cast, so a scene set before someone walks in is
    still told who they are and where they work — which is how a hiring
    manager from scene 3 ends up standing in the office in scene 1.
    """
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    if not wanted:
        return text
    m = re.search(r"^#+\s*(?:Main\s+)?Characters\s*\n(.*?)(?=^#\s|\Z)", text,
                  re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not m:
        return text
    kept = []
    for line in m.group(1).splitlines():
        hit = _CHAR_LINE_RE.match(line.strip())
        if hit and hit.group(1).strip().strip("*_ ").lower() in wanted:
            continue
        kept.append(line)
    section = "\n".join(kept)
    return text[:m.start(1)] + section + text[m.end(1):]


def confusable_name_pairs(names) -> list[tuple[str, str]]:
    """Pairs of character names that are too easy to confuse.

    Catches accidental near-duplicates like 'Dr. Anya Petrov' and 'Elara
    Petrova' — the same Slavic surname in its masculine and feminine form,
    which a plain string comparison misses.
    """
    from difflib import SequenceMatcher

    names = [n for n in names if n and n.split()]
    pairs: list[tuple[str, str]] = []
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            a, b = first.split()[-1].lower(), second.split()[-1].lower()
            if a == b or not a or not b:
                continue  # identical surnames may well be relatives
            ratio = SequenceMatcher(None, a, b).ratio()
            if ratio >= 0.8 or a.startswith(b) or b.startswith(a):
                pairs.append((first, second))
    return pairs


def find_confusable_names(text: str) -> list[tuple[str, str]]:
    """Confusable pairs among the storyboard's listed characters."""
    return confusable_name_pairs([n for n, _desc in extract_characters(text)])


_REVEALS_RE = re.compile(r"^#+\s*Reveals\s*\n(.*?)(?=^#\s|\Z)",
                         re.MULTILINE | re.DOTALL | re.IGNORECASE)
_REVEAL_LINE_RE = re.compile(r"^\s*[-*]?\s*scenes?\s*(\d+)\s*[:.\-–]\s*(.+)$",
                             re.IGNORECASE)


def filter_reveals(text: str, scene_number: int) -> str:
    """Storyboard text with later reveals removed.

    A mystery's plan has to contain the answer, but the scene writer must not
    see it before the reader does. Lines in a '# Reveals' section that are
    marked for a later scene are dropped when writing an earlier one.
    """
    match = _REVEALS_RE.search(text)
    if not match:
        return text
    kept, hidden = [], 0
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        hit = _REVEAL_LINE_RE.match(line)
        if hit and int(hit.group(1)) > scene_number:
            hidden += 1
            continue
        kept.append(line.rstrip())
    body = "\n".join(kept)
    if hidden:
        body += (f"\n({hidden} later reveal(s) withheld — the reader does not "
                 "know them yet, so this scene must not state or hint at them.)")
    section = match.group(0)
    header = section[:section.index("\n") + 1]
    return text[:match.start()] + header + body + "\n\n" + text[match.end():]


def remove_style_guide(text: str) -> str:
    """Storyboard text without its Narrative Style Guide section (which the
    scene prompt already carries separately in {style_guide})."""
    return re.sub(
        r"^#+\s*Narrative Style Guide\s*\n.*?(?=^#\s|\Z)", "", text,
        flags=re.MULTILINE | re.DOTALL,
    ).strip()


# ---------------------------------------------------------------------------
# Story descriptions (from the Detailed Builder tab)
# ---------------------------------------------------------------------------

def list_descriptions() -> list[str]:
    if not DESCRIPTIONS_DIR.is_dir():
        return []
    return sorted(f.name for f in DESCRIPTIONS_DIR.glob("*.txt"))


def load_description(filename: str) -> str:
    path = DESCRIPTIONS_DIR / filename
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def save_description(stem: str, text: str) -> Path:
    path = unique_path(DESCRIPTIONS_DIR, slugify(stem), ".txt")
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Story project
# ---------------------------------------------------------------------------

@dataclass
class Scene:
    title: str = ""
    location: str = ""
    characters: str = ""
    beat: str = ""       # WHAT HAPPENS
    purpose: str = ""
    text: str = ""
    summary: str = ""
    status: str = SCENE_OUTLINED
    summary_stale: bool = False
    # continuity problems found in this scene's text after it was written.
    # Kept on the scene so a batch run's warnings survive to be read later,
    # instead of scrolling past in the status bar.
    issues: list = field(default_factory=list)

    def outline_block(self, number: int) -> str:
        """The scene's plan as sent to the writer; empty fields are omitted so
        the prompt never contains bare labels like 'CHARACTERS:'."""
        lines = [f"SCENE {number}: {self.title}".rstrip()]
        for label, value in (("LOCATION", self.location),
                             ("CHARACTERS", self.characters),
                             ("WHAT HAPPENS", self.beat),
                             ("PURPOSE", self.purpose)):
            value = (value or "").strip()
            if not value:
                continue
            # a field may already carry its own label (unparsed outline text)
            if value.upper().startswith(label + ":"):
                lines.append(value)
            else:
                lines.append(f"{label}: {value}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


@dataclass
class StoryProject:
    name: str = "untitled-story"
    concept: str = ""
    storyboard_name: str = ""   # library reference (for the lorebook)
    storyboard_text: str = ""   # snapshot used for writing
    scenes: list[Scene] = field(default_factory=list)
    num_scenes: int = 6
    target_length: int = 900    # words per scene
    created: float = field(default_factory=time.time)
    gen_info: dict = field(default_factory=dict)  # params/models used per section
    # characters met so far: name -> {"desc": str, "scenes": [1, 3, …]}.
    # Grows as scenes are written so later scenes keep names, roles and
    # pronouns straight. Lives on the story, not the storyboard, so it never
    # pollutes other stories.
    cast: dict = field(default_factory=dict)

    # -- tracked cast helpers ------------------------------------------------

    @staticmethod
    def _cast_record(value) -> dict:
        """Accept both the current dict form and the earlier plain string."""
        if isinstance(value, dict):
            return {"desc": str(value.get("desc", "")),
                    "scenes": [int(s) for s in value.get("scenes", [])],
                    "pronouns": str(value.get("pronouns", ""))}
        return {"desc": str(value), "scenes": [], "pronouns": ""}

    def cast_desc(self, name: str) -> str:
        return self._cast_record(self.cast.get(name, "")).get("desc", "")

    def cast_pronouns(self, name: str) -> str:
        return self._cast_record(self.cast.get(name, "")).get("pronouns", "")

    def set_cast_pronouns(self, name: str, pronouns: str) -> None:
        if name in self.cast:
            rec = self._cast_record(self.cast[name])
            rec["pronouns"] = pronouns.strip()
            self.cast[name] = rec

    def cast_scenes(self, name: str) -> list[int]:
        return self._cast_record(self.cast.get(name, "")).get("scenes", [])

    def cast_first_scene(self, name: str) -> int | None:
        scenes = self.cast_scenes(name)
        return min(scenes) if scenes else None

    def outline_first_scene(self, name: str) -> int | None:
        """The first scene whose plan mentions `name`, or None if never.

        The story board lists the whole cast, so without this every scene is
        told about people it has not met yet — and the writer drops the boss
        of a company the heroine only visits in scene 3 into scene 1.
        """
        words = [w for w in self._name_words(name) if len(w) >= 3]
        if not words:
            return None
        for number, scene in enumerate(self.scenes, 1):
            plan = scene.outline_block(number).lower()
            if any(re.search(r"\b" + re.escape(w) + r"\b", plan) for w in words):
                return number
        return None

    _NAME_TITLES = {"dr", "dr.", "doctor", "professor", "prof", "prof.", "mr",
                    "mr.", "mrs", "mrs.", "ms", "ms.", "miss", "detective",
                    "officer", "captain", "lieutenant", "sergeant"}

    @classmethod
    def _name_words(cls, name: str) -> set:
        words = {w.strip(".,'\"").lower() for w in name.split()}
        return {w for w in words if w and w not in cls._NAME_TITLES}

    def _same_person(self, name: str) -> str:
        """An already-tracked name that refers to the same person.

        A story that reveals an identity calls one woman 'Svetlana' and then
        'Svetlana Doe'; without this they become two cast members and both get
        sent to later scenes.
        """
        words = self._name_words(name)
        if not words:
            return ""
        for existing in self.cast:
            other = self._name_words(existing)
            if other and (words <= other or other <= words):
                return existing
        return ""

    def note_cast(self, name: str, desc: str, scene_number: int | None = None,
                  pronouns: str = "") -> bool:
        """Record a character (and the scene they appear in). True if new."""
        existing = name if name in self.cast else self._same_person(name)
        is_new = not existing
        rec = self._cast_record(self.cast.get(existing, {"desc": desc, "scenes": []}))
        if is_new or not rec["desc"]:
            rec["desc"] = desc
        # first pronouns win: a later scene must not flip an established one
        if pronouns and not rec.get("pronouns"):
            rec["pronouns"] = pronouns.strip()
        if scene_number and scene_number not in rec["scenes"]:
            rec["scenes"].append(scene_number)
            rec["scenes"].sort()
        # keep the fuller name as the canonical one ("Svetlana" -> "Svetlana Doe")
        canonical = name
        if existing:
            canonical = max((existing, name), key=lambda n: len(self._name_words(n)))
            if canonical != existing:
                self.cast.pop(existing, None)
        self.cast[canonical] = rec
        return is_new

    @property
    def title(self) -> str:
        return extract_storyboard_title(self.storyboard_text) or self.name

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StoryProject":
        p = cls()
        for k, v in (d or {}).items():
            if k == "scenes":
                p.scenes = [Scene.from_dict(s) for s in v]
            elif hasattr(p, k):
                setattr(p, k, v)
        return p

    # -- persistence --------------------------------------------------------

    def path(self) -> Path:
        return PROJECTS_DIR / f"{self.name}.json"

    def save(self) -> Path:
        PROJECTS_DIR.mkdir(exist_ok=True)
        self.path().write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return self.path()

    @classmethod
    def load(cls, path: Path) -> "StoryProject":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    # -- export -------------------------------------------------------------

    def combined_text(self, markdown: bool = False) -> str:
        parts = []
        title = self.title
        if markdown:
            parts.append(f"# {title}\n")
        else:
            parts.append(f"{title}\n{'=' * len(title)}\n")
        for i, scene in enumerate(self.scenes, 1):
            if not scene.text.strip():
                continue
            if markdown:
                parts.append(f"\n## Scene {i}: {scene.title}\n")
            else:
                parts.append(f"\nScene {i}: {scene.title}\n{'-' * 30}\n")
            parts.append(scene.text.strip() + "\n")
        return "\n".join(parts)

    def export(self, markdown: bool = False) -> Path:
        suffix = ".md" if markdown else ".txt"
        stem = f"{slugify(self.title)}_{time.strftime('%Y-%m-%d_%H%M')}"
        path = unique_path(OUTPUT_DIR, stem, suffix)
        path.write_text(self.combined_text(markdown=markdown), encoding="utf-8")
        return path


def list_projects() -> list[str]:
    """Project stems, newest first (by file modification time)."""
    if not PROJECTS_DIR.is_dir():
        return []
    files = sorted(PROJECTS_DIR.glob("*.json"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return [f.stem for f in files]


def new_project_name(base: str) -> str:
    """A dated project name (file stem) that does not collide with existing ones."""
    stem = f"{slugify(base)}_{time.strftime('%Y-%m-%d_%H%M')}"
    return unique_path(PROJECTS_DIR, stem, ".json").stem
