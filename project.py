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

@dataclass
class LorebookEntry:
    name: str = ""
    keywords: list[str] = field(default_factory=list)
    content: str = ""
    always_include: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LorebookEntry":
        return cls(
            name=d.get("name", ""),
            keywords=list(d.get("keywords", [])),
            content=d.get("content", ""),
            always_include=bool(d.get("always_include", False)),
        )


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

    def outline_block(self, number: int) -> str:
        return (
            f"SCENE {number}: {self.title}\n"
            f"LOCATION: {self.location}\n"
            f"CHARACTERS: {self.characters}\n"
            f"WHAT HAPPENS: {self.beat}\n"
            f"PURPOSE: {self.purpose}"
        )

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
