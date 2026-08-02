"""Offscreen smoke test: builds the whole UI and exercises non-LLM logic.

Run with:  python test_smoke.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import pipeline
import prompts
from project import LorebookEntry, Scene, StoryProject


def test_outline_parsing():
    text = """SCENE 1: The Body in the Library
LOCATION: Blackwood Manor, the library
CHARACTERS: Inspector Hale, Lady Blackwood
WHAT HAPPENS: A body is found at dawn. Hale arrives and notices the window
was forced from the inside.
PURPOSE: Establish the mystery and the closed circle of suspects.

SCENE 2: Questions at Breakfast
LOCATION: The dining room
CHARACTERS: Inspector Hale, the houseguests
WHAT HAPPENS: Hale interviews the guests. Everyone has an alibi; two of them
contradict each other.
PURPOSE: Introduce the suspects and the first contradiction.
"""
    scenes = pipeline.parse_outline(text)
    assert len(scenes) == 2, f"expected 2 scenes, got {len(scenes)}"
    assert scenes[0].title == "The Body in the Library", scenes[0].title
    assert "forced from the inside" in scenes[0].beat
    assert scenes[1].location == "The dining room", scenes[1].location
    assert "contradiction" in scenes[1].purpose

    # Faithful-Detailed outline format with the extra fields
    detailed = """SCENE 1: The Body in the Library
LOCATION: Blackwood Manor library
TIME: Dawn, the morning after the storm
CHARACTERS: Hale, Lady Blackwood
WHAT HAPPENS: Hale is called in at first light. He examines the body and
notices the window was forced from the inside. Lady Blackwood hovers at the
door, too composed. Hale asks who found the body; her answer contradicts the
maid's account. The scene ends with Hale pocketing a torn cufflink.
KEY DETAILS: A monogrammed cufflink with the letter L; the smell of pipe
smoke although nobody in the house smokes.
CHARACTER FOCUS: Hale is weary but alert; he distrusts Lady Blackwood's calm.
PURPOSE: Establish the mystery and plant the two key clues.
"""
    ds = pipeline.parse_outline(detailed)
    assert len(ds) == 1
    assert ds[0].location == "Blackwood Manor library — Dawn, the morning after the storm"
    assert "Key details: A monogrammed cufflink" in ds[0].beat
    assert "Character focus: Hale is weary" in ds[0].beat
    assert "pipe" in ds[0].beat and "plant the two key clues" in ds[0].purpose
    assert "KEY DETAILS" not in ds[0].characters, "labels leaked into fields"
    print("outline parsing OK (default + detailed format)")


def test_prompt_presets():
    for preset in prompts.BUILTIN_PRESETS:
        for section in prompts.SECTIONS:
            tpl = prompts.get_prompt(preset, section)
            assert tpl["system"] and tpl["user"], (preset, section)
            bad = prompts.find_unknown_placeholders(tpl["system"] + tpl["user"])
            assert not bad, f"unknown placeholders in {preset}/{section}: {bad}"
    # Detailed-Summary overrides only the summary; the rest falls back to Default
    detailed = prompts.get_prompt("Detailed-Summary", "summary")
    assert "250 words" in detailed["user"]
    assert prompts.get_prompt("Detailed-Summary", "scene") == \
        prompts.get_prompt("Default", "scene")
    print("prompt presets OK")


def test_scene_prompt_assembly():
    from backends import SectionConfig
    story = StoryProject(
        name="test",
        storyboard_text=(
            "# Title\nThe Blackwood Affair\n\n# Narrative Style Guide\n"
            "Third person limited, past tense, dry wit.\n"
        ),
        scenes=[
            Scene(title="One", beat="The body is found.",
                  text="A long first scene. " * 200, summary="The body was found."),
            Scene(title="Two", beat="Hale questions Lady Blackwood.",
                  text="Second scene text. " * 200, summary="Hale asked questions."),
            Scene(title="Three", beat="A second body appears."),
        ],
        num_scenes=3,
    )
    lore = [
        LorebookEntry(name="Inspector Hale", keywords=["Hale"],
                      content="A retired opera singer turned detective."),
        LorebookEntry(name="The Ruby", keywords=["ruby"],
                      content="Stolen in 1911.", always_include=True),
    ]
    cfg = SectionConfig()
    cfg.context_length = 8192
    system, user = pipeline.build_scene_prompts(cfg, story, 2, lore)
    assert "dry wit" in system, "style guide missing"
    assert system.count("dry wit") == 1, \
        "style guide appears twice (should be stripped from the storyboard)"
    assert "The Blackwood Affair" in system, "storyboard content missing"
    assert "The Ruby" in system, "always-include lorebook entry missing"
    assert "Scene 1 (One): The body was found." in user, "summary missing"
    assert "Scene 2 (Two): Hale asked questions." in user, \
        "previous scene's summary missing (only its tail was sent)"
    assert "Second scene text." in user, "previous tail missing"
    assert "A second body appears." in user, "scene beat missing"
    assert "{" not in system.replace("{concept}", ""), "unfilled placeholder?"

    # context report: simple verdict line + detailed tooltip breakdown
    short, detail = pipeline.context_report(cfg, story, 2, lore)
    assert "OK ✓" in short, short
    assert "Summaries of 2 earlier scene(s)" in detail, detail
    assert "space reserved for the reply" in detail, detail
    cfg_small = SectionConfig()
    cfg_small.context_length = 1024
    short2, detail2 = pipeline.context_report(cfg_small, story, 2, lore)
    assert "TOO BIG" in short2, short2
    assert "lowering the Scene Writer's Max tokens" in short2, \
        "oversized reply reserve should trigger the Max-tokens hint"
    assert "Tip: Max tokens" in detail2, detail2

    # tiny budget forces the previous-scene tail to be dropped
    cfg.context_length = 1500
    cfg.params.max_tokens = 1024
    system2, user2 = pipeline.build_scene_prompts(cfg, story, 2, lore)
    assert "Second scene text." not in user2, "previous tail should have been dropped"
    assert "A second body appears." in user2, "scene beat must survive trimming"
    assert pipeline.estimate_tokens(system2 + user2) < pipeline.estimate_tokens(system + user)
    print("scene prompt assembly OK")


def test_character_parsing():
    text = """Inspector Hale: A retired opera singer turned detective. Sharp-eyed.
- **Lady Blackwood**: The widow of the manor. Hiding a debt.
Not a card line
"""
    entries = pipeline.parse_character_entries(text)
    names = [e.name for e in entries]
    assert "Inspector Hale" in names and "Lady Blackwood" in names, names
    print("character parsing OK")


def test_log_trim():
    import applog
    orig_file, orig_max = applog.LOG_FILE, applog.MAX_BYTES
    from project import APP_DIR
    applog.LOG_FILE = APP_DIR / "log-trim-test.log"
    applog.MAX_BYTES = 4000
    try:
        for i in range(200):
            applog.log("test", f"entry number {i} " + "x" * 60)
        size = applog.LOG_FILE.stat().st_size
        assert size < 8000, f"log grew unbounded: {size} bytes"
        text = applog.read_log()
        assert "older entries trimmed" in text
        assert "entry number 199" in text, "newest entries must survive trimming"
        assert "entry number 0 " not in text, "oldest entries should be gone"
        # trimming cuts at a line boundary — every line starts with a timestamp
        for line in text.strip().splitlines():
            assert line[:2] == "20", f"line cut mid-entry: {line[:40]}"
    finally:
        applog.LOG_FILE.unlink(missing_ok=True)
        applog.LOG_FILE, applog.MAX_BYTES = orig_file, orig_max
    print("log size cap OK")


def test_infinite_spin():
    app = QApplication.instance() or QApplication(sys.argv)
    from ui_common import make_infinite_spin
    s = make_infinite_spin(600, 0, 7200, 10)
    assert s.valueFromText("infinite") == 0
    assert s.valueFromText("INFINITE") == 0
    assert s.valueFromText("300") == 300
    from PySide6.QtGui import QValidator
    state, _, _ = s.validate("infinite", 8)
    assert state == QValidator.Acceptable
    state, _, _ = s.validate("inf", 3)
    assert state == QValidator.Intermediate
    s.setValue(0)
    assert s.text() == "infinite"
    print("infinite spinbox OK")


def test_strip_think():
    from backends import strip_think
    assert strip_think("<think>plan</think>Story text.") == "Story text."
    assert strip_think("Story only.") == "Story only."
    assert strip_think("<think>never closed, all reasoning") == ""
    assert strip_think("A<think>x</think>B<think>y</think>C") == "ABC"
    print("strip_think OK")


def test_project_roundtrip(tmp_ok=True):
    story = StoryProject(name="smoke-test-story", concept="a test",
                         storyboard_text="# Title\nSmoke Test\n")
    story.scenes = [Scene(title="A", text="Once upon a time."),
                    Scene(title="B", text="The end.")]
    path = story.save()
    loaded = StoryProject.load(path)
    assert loaded.scenes[1].text == "The end."
    combined = loaded.combined_text()
    assert "Smoke Test" in combined and "Once upon a time." in combined
    path.unlink()
    print("project save/load OK")


def test_writer_defaults():
    from backends import default_section_params
    p = default_section_params("writer")
    assert p.ranges["temperature"] == [0.7, 1.0]
    assert p.ranges["smoothing_factor"] == [0.15, 0.3], \
        "writer default should include mild smoothing (kobold-only)"
    assert p.max_tokens == 4096
    # planning sections stay neutral — no smoothing
    assert default_section_params("planner").ranges["smoothing_factor"] == [0.0, 0.0]
    assert default_section_params("summarizer").ranges["smoothing_factor"] == [0.0, 0.0]
    print("writer defaults OK")


def test_param_ranges():
    from backends import GenParams, SectionConfig
    p = GenParams()
    p.ranges["temperature"] = [0.6, 1.3]
    rolled = p.rolled()
    lo, hi = rolled.ranges["temperature"]
    assert lo == hi and 0.6 <= lo <= 1.3, rolled.ranges["temperature"]
    # rolled twice on the same rolled params must not change (story-stable)
    again = rolled.rolled()
    assert again.ranges["temperature"] == rolled.ranges["temperature"]
    # old flat settings format migrates into ranges
    old = GenParams.from_dict({"temperature": 0.7, "top_k": 50, "max_tokens": 1024})
    assert old.ranges["temperature"] == [0.7, 0.7]
    assert old.ranges["top_k"] == [50, 50] and old.max_tokens == 1024
    # kobold extras included in payload only for koboldcpp
    import backends
    cfg = SectionConfig(backend="koboldcpp")
    cfg.params.ranges["smoothing_factor"] = [0.3, 0.3]
    _, payload = backends._build_payload(cfg, "s", "u")
    assert payload["smoothing_factor"] == 0.3 and "rep_pen" in payload
    cfg2 = SectionConfig(backend="llama.cpp")
    cfg2.params.ranges["smoothing_factor"] = [0.3, 0.3]
    _, payload2 = backends._build_payload(cfg2, "s", "u")
    assert "smoothing_factor" not in payload2
    print("param ranges + kobold extras OK")


def test_full_context_mode():
    from backends import SectionConfig
    story = StoryProject(
        name="t2", storyboard_text="# Title\nT\n",
        scenes=[Scene(title="One", beat="b1", text="FULL SCENE ONE TEXT HERE.",
                      summary="short summary one"),
                Scene(title="Two", beat="b2", text="scene two text",
                      summary="short summary two"),
                Scene(title="Three", beat="b3")])
    cfg = SectionConfig()
    _, user_sum = pipeline.build_scene_prompts(cfg, story, 2, [],
                                               context_mode="summaries")
    assert "short summary one" in user_sum and "FULL SCENE ONE" not in user_sum
    _, user_full = pipeline.build_scene_prompts(cfg, story, 2, [],
                                                context_mode="full")
    assert "FULL SCENE ONE TEXT HERE." in user_full
    assert "scene two text" in user_full, "previous scene's full text missing"
    assert "previous scene appears in full above" in user_full, \
        "full-mode tail note missing"

    # prev_full: summaries of OLDER scenes + the previous scene complete
    _, user_pf = pipeline.build_scene_prompts(cfg, story, 2, [],
                                              context_mode="prev_full")
    assert "short summary one" in user_pf, "older scene summary missing"
    assert "short summary two" not in user_pf, \
        "previous scene should appear in full, not as a summary too"
    assert "scene two text" in user_pf, "previous scene full text missing"
    assert "FULL SCENE ONE TEXT HERE." not in user_pf, \
        "older scenes must stay summarized in prev_full mode"

    # scene 2 in prev_full mode: nothing older to summarize, scene 1 in full
    story2 = StoryProject(
        name="t3", storyboard_text="# Title\nT\n",
        scenes=[Scene(title="One", beat="b1", text="ONE FULL TEXT",
                      summary="sum one"),
                Scene(title="Two", beat="b2")])
    _, user_pf2 = pipeline.build_scene_prompts(cfg, story2, 1, [],
                                               context_mode="prev_full")
    assert "ONE FULL TEXT" in user_pf2
    assert "nothing earlier to summarize" in user_pf2, \
        "wrong placeholder when only the previous scene exists"
    assert "this is the first scene" not in user_pf2, \
        "scene 2 must not be told it is the first scene"
    print("full-context mode OK")


def test_cast_extraction():
    from project import extract_characters
    board = """# Title
The Unbreakable Schedule

# Main Characters
**Clara Vance** — Senior Administrative Secretary, mid-40s, precise.
**Alistair Finch** — CEO of OmniCorp, cold and clipped.
- **Brenda Hayes**: Recruiter, kinetic and impatient.

# Plot Summary
Beginning: she is fired.
"""
    cast = extract_characters(board)
    names = [n for n, _ in cast]
    assert names == ["Clara Vance", "Alistair Finch", "Brenda Hayes"], names
    assert "Senior Administrative Secretary" in cast[0][1]
    assert extract_characters("# Title\nNo cast here\n") == []

    # the cast fills the lorebook slot when no lorebook entries exist
    from backends import SectionConfig
    story = StoryProject(name="c", storyboard_text=board,
                         scenes=[Scene(title="One", beat="b")])
    system, _ = pipeline.build_scene_prompts(SectionConfig(), story, 0, [])
    assert "these names, roles and genders are fixed" in system
    assert "Alistair Finch" in system
    print("cast extraction OK")


def test_cast_tracking():
    from backends import SectionConfig
    story = StoryProject(
        name="ct",
        storyboard_text="# Title\nT\n\n# Main Characters\n**Clara Thorne** — secretary.\n",
        scenes=[Scene(title="One", beat="b1", text="prose"),
                Scene(title="Two", beat="b2")])
    added = pipeline.merge_cast(story, {"Liam Reyes": "hiring manager at Zenith, male",
                                        "Clara Thorne": "should not overwrite"},
                                scene_number=1)
    assert added == 2 and story.cast_desc("Liam Reyes").startswith("hiring manager")
    assert story.cast_first_scene("Liam Reyes") == 1
    # seen again in a later scene: not new, but the appearance is recorded
    assert pipeline.merge_cast(story, {"Liam Reyes": "different text"}, 2) == 0
    assert story.cast_scenes("Liam Reyes") == [1, 2]
    assert story.cast_first_scene("Liam Reyes") == 1, "first scene must not move"
    # tracked cast reaches the scene prompt with its first-appearance note
    system, _ = pipeline.build_scene_prompts(SectionConfig(), story, 1, [])
    assert "Liam Reyes" in system and "Clara Thorne" in system
    assert "since scene 1" in system
    assert "never rename or re-invent them" in system
    # cast survives a save/load round trip
    path = story.save()
    reloaded = StoryProject.load(path)
    assert reloaded.cast_desc("Liam Reyes").startswith("hiring manager")
    assert reloaded.cast_scenes("Liam Reyes") == [1, 2]
    path.unlink()
    # an identity reveal must not create a second cast member
    reveal = StoryProject(name="reveal")
    reveal.note_cast("Svetlana", "victim, female", 1)
    assert reveal.note_cast("Svetlana Doe", "victim identified", 3) is False
    assert list(reveal.cast) == ["Svetlana Doe"], reveal.cast
    assert reveal.cast_scenes("Svetlana Doe") == [1, 3]
    assert reveal.cast_first_scene("Svetlana Doe") == 1
    # titles do not fork a character either
    reveal.note_cast("Dr. Moreau", "dental forensics, male", 3)
    assert reveal.note_cast("Moreau", "same man", 4) is False
    assert reveal.cast_scenes("Dr. Moreau") == [3, 4]
    # genuinely different people stay separate, even sharing a surname
    reveal.note_cast("Sofia Rostova", "sous chef, female", 4)
    assert reveal.note_cast("Elina Rostova", "her sister, female", 5) is True
    assert "Sofia Rostova" in reveal.cast and "Elina Rostova" in reveal.cast

    # stories saved before scene tracking (plain strings) still load
    legacy = StoryProject(name="legacy")
    legacy.cast = {"Old Name": "a plain string description"}
    assert legacy.cast_desc("Old Name") == "a plain string description"
    assert legacy.cast_first_scene("Old Name") is None
    print("cast tracking OK")


def test_pronoun_tracking():
    from backends import SectionConfig
    from project import LorebookEntry

    # pronouns are parsed out of the extractor's "Name (she/her): facts" line
    assert pipeline._split_pronouns("Elara Petrova (she/her)") == \
        ("Elara Petrova", "she/her")
    assert pipeline._split_pronouns("Dr. Moreau (he/him)") == ("Dr. Moreau", "he/him")
    assert pipeline._split_pronouns("Clara Davies") == ("Clara Davies", "")

    story = StoryProject(name="p", storyboard_text="# Title\nT\n",
                         scenes=[Scene(title="One", beat="b", text="x"),
                                 Scene(title="Two", beat="b2")])
    pipeline.merge_cast(story, {
        "Clara Davies": {"desc": "the friend", "pronouns": "she/her"}}, 1)
    assert story.cast_pronouns("Clara Davies") == "she/her"
    # a later scene must not flip an established gender
    pipeline.merge_cast(story, {
        "Clara Davies": {"desc": "the friend", "pronouns": "he/him"}}, 2)
    assert story.cast_pronouns("Clara Davies") == "she/her"
    # …and they reach the scene prompt
    system, _ = pipeline.build_scene_prompts(SectionConfig(), story, 1, [])
    assert "Clara Davies (she/her)" in system, system

    # lorebook entries carry pronouns too, and survive save/load
    entry = LorebookEntry(name="Silas Thorne", content="the boss",
                          pronouns="he/him", always_include=True)
    assert entry.as_fact_line() == "- Silas Thorne (he/him): the boss"
    assert LorebookEntry.from_dict(entry.to_dict()).pronouns == "he/him"
    # entries written before this field still load
    assert LorebookEntry.from_dict({"name": "Old", "content": "c"}).pronouns == ""
    print("pronoun tracking OK")


def test_outline_block():
    s = Scene(title="The Deficit Report", beat="She counts what is left.")
    block = s.outline_block(3)
    assert "SCENE 3: The Deficit Report" in block
    assert "WHAT HAPPENS: She counts what is left." in block
    assert "CHARACTERS" not in block, "empty fields must be omitted"
    assert "PURPOSE" not in block, "empty fields must be omitted"
    # a field that already carries its label must not be doubled
    s2 = Scene(title="X", beat="WHAT HAPPENS: She leaves.", purpose="To end it.")
    b2 = s2.outline_block(1)
    assert "WHAT HAPPENS: WHAT HAPPENS" not in b2, b2
    assert "PURPOSE: To end it." in b2
    print("outline block formatting OK")


def test_confusable_name_detection():
    from project import find_confusable_names
    board = ("# Main Characters\n"
             "**Dr. Anya Petrov** — the medical examiner, female.\n"
             "**Elara Petrova** — the victim, a tailor.\n"
             "**Vera Holstrom** — the detective.\n")
    pairs = find_confusable_names(board)
    flat = {tuple(sorted(p)) for p in pairs}
    assert ("Dr. Anya Petrov", "Elara Petrova") in flat, pairs
    assert len(pairs) == 1, f"Holstrom should not be flagged: {pairs}"

    # relatives sharing an identical surname are legitimate, not flagged
    sisters = ("# Main Characters\n"
               "**Sofia Rostova** — the sous chef.\n"
               "**Elina Rostova** — her sister.\n")
    assert find_confusable_names(sisters) == []
    # ordinary distinct casts stay quiet
    plain = ("# Main Characters\n"
             "**Maya Thorne** — detective.\n"
             "**Karin Volsky** — the victim.\n")
    assert find_confusable_names(plain) == []
    # the real case: the victim's name only appears once the story reveals it,
    # so the clash shows up in the tracked cast rather than the storyboard
    from project import confusable_name_pairs
    tracked = ["Vera Holstrom", "Marcus Thorne", "Anya Petrov", "Elara Petrova"]
    flagged = {tuple(sorted(p)) for p in confusable_name_pairs(tracked)}
    assert ("Anya Petrov", "Elara Petrova") in flagged, flagged
    assert len(flagged) == 1, flagged
    print("confusable name detection OK")


def test_reveal_gating():
    from project import filter_reveals
    board = (
        "# Title\nThe Echo\n\n"
        "# Main Characters\n**Jane Doe** — the victim, unidentified.\n\n"
        "# Reveals\n"
        "Scene 3: the laundry tag gives the name Elara\n"
        "Scene 5: her full name is Elara Petrova\n"
        "Scene 6: the killer is her apprentice\n\n"
        "# Themes\nIdentity.\n")
    early = filter_reveals(board, 2)
    assert "Elara" not in early, early
    assert "3 later reveal(s) withheld" in early
    assert "# Themes" in early and "Jane Doe" in early, "rest of the board kept"

    mid = filter_reveals(board, 3)
    assert "the laundry tag gives the name Elara" in mid
    assert "Elara Petrova" not in mid
    assert "2 later reveal(s) withheld" in mid

    late = filter_reveals(board, 6)
    assert "the killer is her apprentice" in late
    assert "withheld" not in late
    # a board with no Reveals section is untouched
    plain = "# Title\nX\n\n# Themes\nY\n"
    assert filter_reveals(plain, 1) == plain
    print("reveal gating OK")


def test_scene_reference_detection():
    f = pipeline.find_scene_reference
    # the plan leaking into the prose
    assert f("a printout taken during Scene 2: a photograph") == "during Scene 2"
    assert f("the machine of scene two and the collaborator") == "of scene two"
    assert f("As in Chapter 3, she hesitated.") == "in Chapter 3"
    # ordinary prose that merely uses the words
    assert f("She surveyed the crime scene carefully.") == ""
    assert f("The scene was a mess of broken glass.") == ""
    assert f("He read chapter after chapter of the ledger.") == ""
    print("scene reference detection OK")


def test_placeholder_stub_detection():
    f = pipeline.find_placeholder_stub
    # stubs the model leaves instead of writing something
    assert f("She read the Cyrillic characters: [some letters] on the tag.") \
        == "[some letters]"
    assert f("He greeted [insert name] warmly.") == "[insert name]"
    assert f("The sign read [TODO] in red paint.") == "[TODO]"
    # real prose with brackets must not be flagged
    assert f("The report [1962, unsigned] lay on the desk.") == ""
    assert f("She whispered his name. He did not answer.") == ""
    print("placeholder stub detection OK")


def test_role_label_and_honorific_detection():
    label = pipeline.find_role_label
    assert label("When Interviewer B sighed audibly, she paused.") == "Interviewer B"
    assert label("Suspect A refused to speak.") == "Suspect A"
    assert label("Witness 2 gave a statement.") == "Witness 2"
    # real prose naming real people must survive
    assert label("The interviewer, Julian Hayes, steepled his fingers.") == ""
    assert label("Officer Clara Jensen moved around the periphery.") == ""

    clash = pipeline.find_honorific_conflict
    assert clash("A photograph of Mr. Davies. The smiling face of "
                 "Ms. Clara Davies.") != ""
    # a married couple is not a mistake
    assert clash("Mr. Davies poured the tea while Mrs. Davies read.") == ""
    assert clash("Ms. Vance met Mr. Thorne in the lobby.") == ""
    print("role label + honorific conflict detection OK")


def test_repeated_opening_detection():
    f = pipeline.find_repeated_opening
    prev = ("She walked to the window and watched the rain. "
            "I am fully willing to accept the premise that Campaign Gamma "
            "requires my dedicated focus Tuesday evening.")
    # the new scene opens by re-quoting the previous scene's closing line
    repeated = ("I am fully willing to accept the premise that Campaign Gamma "
                "requires my dedicated focus Tuesday evening, she repeated.")
    hit = f(repeated, prev)
    assert "Campaign Gamma" in hit and len(hit) >= 60, hit
    # a genuine continuation that only refers back is not flagged
    fresh = ("The morning after the interview, she took the long way to the "
             "station and thought about what she had conceded.")
    assert f(fresh, prev) == ""
    # short coincidental overlaps are ignored
    assert f("She walked to the door.", prev) == ""
    print("repeated-opening detection OK")


def test_author_aside_detection():
    f = pipeline.find_author_aside
    hit = f("The hiring manager, David Hayes (no relation to Clara's husband), "
            "waits across the table.")
    assert "no relation" in hit, hit
    assert f("She read the note (twice) before answering.") == ""
    assert f("He paused (a habit she had never liked) and looked up.") == ""
    print("author-aside detection OK")


def test_renamed_entity_detection():
    f = pipeline.find_renamed_entity
    established = ("Clara spent fifteen years at Sterling & Finch. "
                   "The Sterling & Finch offices were on the tenth floor. "
                   "Marcus Thorne ran Sterling & Finch like a museum.")
    drift = "Since Stellar & Finch historically siloed finance, she proposed."
    hit = f(drift, established)
    assert "Stellar & Finch" in hit and "Sterling & Finch" in hit, hit
    # a genuinely new company is not a misspelling of the old one
    assert f("She interviewed at Apex Solutions.", established) == ""
    # the established name itself is fine
    assert f("She returned to Sterling & Finch.", established) == ""
    # a name used only once is not established enough to compare against
    assert f("Stellar & Finch called back.",
             "Clara left Sterling & Finch.") == ""
    print("renamed-entity detection OK")


def test_formulaic_opening_detection():
    f = pipeline.find_formulaic_opening
    prev = "Clara rises from the imposing chair at Apex Solutions, exhausted."
    same = "Clara rises slowly from the vinyl stool at The Daily Grind."
    assert f(same, prev) == "clara rises", f(same, prev)
    fresh = "The low hum of the refrigerator fills the kitchen."
    assert f(fresh, prev) == ""
    # a shared opening article alone is not a pattern
    assert f("The rain fell.", "The morning came slowly.") == ""
    print("formulaic-opening detection OK")


def test_scene_issue_collection():
    from project import StoryProject, Scene
    story = StoryProject(name="issues", storyboard_text="")
    story.scenes = [Scene(title="One", text="She waited by the door."),
                    Scene(title="Two")]
    issues = pipeline.check_scene(story, 1, "As established in Scene 1, [insert name] arrived.")
    assert len(issues) == 2, issues
    assert any("Scene 1" in i for i in issues), issues
    assert any("placeholder" in i for i in issues), issues
    assert all(i.startswith("⚠ Scene 2") for i in issues), issues
    assert pipeline.check_scene(story, 1, "She opened the door and left.") == []
    print("scene issue collection OK")


def test_upcoming_cast_withheld():
    from backends import SectionConfig
    from project import StoryProject, Scene
    cfg = SectionConfig()
    cfg.context_length = 8192
    story = StoryProject(name="upcoming")
    story.storyboard_text = (
        "# Main Characters\n"
        "- Clara Vance: the secretary\n"
        "- Brenda Holloway: the hiring manager at Apex\n"
    )
    story.scenes = [
        Scene(title="Termination", beat="Clara clears her desk alone."),
        Scene(title="Interview", beat="Brenda Holloway interviews Clara."),
    ]
    system, user = pipeline.build_scene_prompts(cfg, story, 0, [])
    both = system + user
    assert "Clara Vance" in both
    # she belongs to scene 2 — scene 1 may not put her in the office
    assert "Not in the story yet" in both, both
    assert "Brenda Holloway (scene 2)" in both, both
    assert "the hiring manager at Apex" not in both, both
    # by her own scene she is fully described
    system2, user2 = pipeline.build_scene_prompts(cfg, story, 1, [])
    assert "the hiring manager at Apex" in system2 + user2
    assert "Not in the story yet" not in system2 + user2
    print("upcoming-cast withholding OK")


def test_strip_scene_artifacts():
    f = pipeline.strip_scene_artifacts
    assert f("# Scene 4\n\nThe rain fell.", "The Interview") == "The rain fell."
    assert f("Scene 4: The Interview\nThe rain fell.", "The Interview") == "The rain fell."
    assert f("---\n\nThe rain fell.\n\n---", "X") == "The rain fell."
    assert f("**The Interview**\n\nThe rain fell.", "The Interview") == "The rain fell."
    # real prose must never be touched
    keep = "Scene of the crime, she thought. The rain fell."
    assert f(keep, "The Interview") == keep
    assert f("The rain fell.\n\nShe left.", "X") == "The rain fell.\n\nShe left."
    print("scene artifact stripping OK")


def test_language_option():
    from backends import SectionConfig
    cfg = SectionConfig()
    story = StoryProject(
        name="t3", storyboard_text="# Title\nT\n",
        scenes=[Scene(title="One", beat="b1")])
    # language instruction lands in the scene system prompt
    import backends as _b
    captured = {}
    orig = _b.stream_generate
    _b.stream_generate = lambda cfg, system, user, **kw: captured.update(
        system=system, user=user) or "x"
    try:
        pipeline.generate_scene(cfg, story, 0, [], language="Svenska")
        assert "Svenska" in captured["system"], "language missing from scene prompt"
        pipeline.generate_scene(cfg, story, 0, [], language="English")
        assert "Write all story prose in" not in captured["system"], \
            "English must not add a language note"
        pipeline.generate_summary(cfg, "text", language="Svenska")
        assert "Svenska" in captured["user"]
        pipeline.generate_storyboard(cfg, "idea", 3, language="Svenska",
                                     plan_in_language=True)
        assert "Svenska" in captured["system"]
        pipeline.generate_storyboard(cfg, "idea", 3, language="Svenska",
                                     plan_in_language=False)
        assert "Svenska" not in captured["system"], \
            "storyboard must stay English unless plan_in_language is on"
    finally:
        _b.stream_generate = orig
    print("language option OK")


def test_ui_builds():
    app = QApplication.instance() or QApplication(sys.argv)
    import app as appmod
    from project import APP_DIR
    # isolate the test from the user's real settings.json
    test_settings = APP_DIR / "settings.test.json"
    if test_settings.exists():
        test_settings.unlink()
    appmod.SETTINGS_FILE = test_settings
    win = appmod.MainWindow()
    assert win.tabs.count() == 11, f"expected 11 tabs, got {win.tabs.count()}"
    assert "summarizer" in win.state.sections
    # builder compose includes the writing-style options
    desc = win.tab_builder.compose()
    assert "Genre: Fantasy" in desc
    assert "Writing style requirements:" in desc
    assert "Dialogue vs description:" in desc and "Pacing:" in desc
    assert "Ending:" not in desc  # default "Let the story decide" adds no line
    # settings roundtrip with a parameter range
    # the status bar the section widgets talk to must be the live one
    win.statusBar().showMessage("probe")
    win.tab_start.section_widgets["writer"].status_cb("probe two")
    assert win.statusBar().currentMessage() == "probe two", \
        "section widgets are wired to a dead status bar"
    # a model refresh against a dead server must not raise, just report
    win.tab_start.section_widgets["writer"].cfg.backend = "koboldcpp"
    win.state.backends_cfg["koboldcpp"].base_url = "http://127.0.0.1:9"  # closed port
    win.tab_start.section_widgets["writer"].refresh_models()  # must not raise
    assert "is the server running?" in win.statusBar().currentMessage(), \
        win.statusBar().currentMessage()

    # editable description files: preview edits reach get_concept and the file
    import project as prj_mod
    desc_path = prj_mod.save_description("smoke-desc-test", "original idea text")
    win.state.descriptions_changed.emit()
    win.tab_start.radio_file.setChecked(True)
    idx = win.tab_start.file_box.findText(desc_path.name)
    assert idx >= 0, "saved description not listed in the dropdown"
    win.tab_start.file_box.setCurrentIndex(idx)
    assert win.tab_start.file_preview.toPlainText() == "original idea text"
    win.tab_start.file_preview.setPlainText("edited idea text")
    assert win.tab_start.get_concept() == "edited idea text"
    assert desc_path.read_text(encoding="utf-8") == "edited idea text", \
        "preview edits were not written back to the description file"
    win.tab_start.radio_text.setChecked(True)
    desc_path.unlink()

    # each source mode shows only its own panel
    ts = win.tab_start
    ts.radio_board.setChecked(True)
    assert ts.free_panel.isHidden() and ts.file_panel.isHidden()
    assert not ts.board_panel.isHidden()
    ts.radio_file.setChecked(True)
    assert ts.free_panel.isHidden() and ts.board_panel.isHidden()
    assert not ts.file_panel.isHidden()
    ts.radio_text.setChecked(True)
    assert not ts.free_panel.isHidden()
    assert ts.file_panel.isHidden() and ts.board_panel.isHidden()

    # the lorebook tab shows the story's automatically tracked cast
    story_lb = StoryProject(name="lb-test", storyboard_text="# Title\nT\n",
                            scenes=[Scene(title="One", beat="b")])
    story_lb.note_cast("Liam Reyes", "hiring manager, male", 3)
    win.state.project = story_lb
    win.state.selected_storyboard = ""
    lb = win.tab_lorebook
    lb.refresh_cast()
    assert lb.cast_list.count() == 1
    # the list shows the name (with its first scene), details go on the right
    assert lb.cast_list.item(0).text() == "Liam Reyes   (scene 3)"
    lb.cast_list.setCurrentRow(0)
    lb._cast_selected(0)
    assert lb.right_stack.currentIndex() == 1, "cast page should be shown"
    assert lb.cast_name_label.text() == "Liam Reyes"
    assert lb.cast_first_label.text() == "Scene 3"
    assert "hiring manager" in lb.cast_desc_edit.toPlainText()
    # forgetting a wrongly detected character removes it
    lb._forget_cast_entry()
    assert story_lb.cast == {}
    win.state.project = None

    # continuity warnings found while writing stay visible on the scene
    story_iss = StoryProject(name="iss-test", storyboard_text="# Title\nT\n")
    story_iss.scenes = [
        Scene(title="One", text="She waited.", status=prj_mod.SCENE_WRITTEN),
        Scene(title="Two", text="As shown in Scene 1, she left.",
              status=prj_mod.SCENE_WRITTEN,
              issues=["⚠ Scene 2 refers to the story's own plan"]),
    ]
    win.state.project = story_iss
    wt = win.tab_writer
    wt.refresh()
    assert "⚠" not in wt.list.item(0).text(), wt.list.item(0).text()
    assert wt.list.item(1).text().endswith("⚠"), wt.list.item(1).text()
    wt.list.setCurrentRow(1)
    wt._selected(1)
    assert not wt.issues_label.isHidden()
    assert "own plan" in wt.issues_label.text()
    wt.list.setCurrentRow(0)
    wt._selected(0)
    assert wt.issues_label.isHidden(), "a clean scene must show no warning"
    win.state.project = None

    # quick setup applies a preset combination to all four sections
    win.state.sections["planner"].params.max_tokens = 1536
    win.tab_prompts._quick_setup_clicked(1)  # "Detailed & faithful"
    assert win.state.sections["storyboard"].prompt_preset == "Faithful-Detailed"
    assert win.state.sections["planner"].prompt_preset == "Faithful-Detailed"
    assert win.state.sections["writer"].prompt_preset == "Default"
    assert win.state.sections["summarizer"].prompt_preset == "Detailed-Summary"
    assert win.state.sections["planner"].params.max_tokens == 4096, \
        "quick setup should raise the planner's Max tokens"
    win.tab_prompts._sync_quick_radios()
    assert win.tab_prompts.quick_radios[1].isChecked()
    # changing one dropdown makes it a custom mix — no quick radio selected
    win.tab_prompts.use_boxes["writer"].setCurrentText("Qwen-tuned")
    assert not any(rb.isChecked() for rb in win.tab_prompts.quick_radios)

    win.state.sections["writer"].params.ranges["temperature"] = [0.61, 1.29]
    win.state.save_settings()
    win.state.load_settings()
    assert win.state.sections["writer"].params.ranges["temperature"] == [0.61, 1.29]
    win.close()
    test_settings.unlink(missing_ok=True)
    print("UI builds OK (11 tabs), settings persist OK")


if __name__ == "__main__":
    test_outline_parsing()
    test_prompt_presets()
    test_scene_prompt_assembly()
    test_character_parsing()
    test_writer_defaults()
    test_param_ranges()
    test_full_context_mode()
    test_language_option()
    test_cast_extraction()
    test_cast_tracking()
    test_pronoun_tracking()
    test_outline_block()
    test_confusable_name_detection()
    test_reveal_gating()
    test_scene_reference_detection()
    test_placeholder_stub_detection()
    test_role_label_and_honorific_detection()
    test_repeated_opening_detection()
    test_author_aside_detection()
    test_renamed_entity_detection()
    test_formulaic_opening_detection()
    test_scene_issue_collection()
    test_upcoming_cast_withheld()
    test_strip_scene_artifacts()
    test_log_trim()
    test_infinite_spin()
    test_strip_think()
    test_project_roundtrip()
    test_ui_builds()
    print("ALL SMOKE TESTS PASSED")
