"""End-to-end pipeline test against a mock OpenAI-compatible SSE server.

Verifies the exact HTTP/streaming path used with koboldcpp / llama-server /
ollama, without needing a real model.  Run with:  python test_mock_llm.py
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pipeline
from backends import SectionConfig, TEMPLATE_MODE_MANUAL
from project import Scene, StoryProject

MOCK_STORYBOARD = """# Title
The Blackwood Affair

# Logline
A retired opera singer turned detective must solve a locked-room murder.

# Genre & Tone
Detective whodunit, serious with dry wit.

# Setting
Blackwood Manor, England, 1926.

# Main Characters
**Inspector Hale** — detective, sharp-eyed, motivated by an old debt of honor.
**Lady Blackwood** — widow, gracious, hiding gambling debts.

# Plot Summary
Beginning: A body is found. Middle: Alibis crumble. End: The culprit confesses.

# Themes
Guilt, appearances, class.

# Narrative Style Guide
Third person limited (Hale), past tense, dry wit, short scenes.
"""

MOCK_OUTLINE = """SCENE 1: The Body in the Library
LOCATION: Blackwood Manor library
CHARACTERS: Hale, Lady Blackwood
WHAT HAPPENS: A body is found at dawn; Hale notices the window was forced from inside.
PURPOSE: Establish the mystery.

SCENE 2: Questions at Breakfast
LOCATION: Dining room
CHARACTERS: Hale, the guests
WHAT HAPPENS: Interviews reveal two contradicting alibis.
PURPOSE: Introduce the suspects.

SCENE 3: The Confession
LOCATION: The conservatory
CHARACTERS: Hale, all suspects
WHAT HAPPENS: Hale lays out the evidence and the culprit confesses.
PURPOSE: Resolution.
"""

MOCK_SCENE = ("The library smelled of cold ash and old paper. " * 30).strip()
# Swedish characters + em-dash to verify the UTF-8 stream decoding
MOCK_SUMMARY = ("Hale examined the library at kvällstid — found the forced window "
                "and suspected an inside job. Händelseförlopp: åtta spår, ödesdigert.")


def pick_response(system: str, user: str) -> str:
    blob = (system + "\n" + user).lower()
    if "inline-think-test" in blob:
        return "<think>secret reasoning plan</think>The real story begins here."
    if "story board" in blob or "storyboard" in blob and "scene" not in blob[:200]:
        pass
    if "list every character in this scene" in blob:
        return ("Inspector Hale (he/him): lead detective, retired opera singer.\n"
                "Lady Blackwood (she/her): widow of the manor, hiding debts.\n"
                "Beginning: this heading must be filtered out.")
    if "break the following story into" in blob:
        return MOCK_OUTLINE
    if "summarize the following scene" in blob:
        return MOCK_SUMMARY
    # match several phrasings so prompt rewording doesn't silently turn scene
    # requests into storyboard responses
    if ("write scene" in blob or "now write scene" in blob
            or "write the full prose" in blob):
        return MOCK_SCENE
    return MOCK_STORYBOARD


class Handler(BaseHTTPRequestHandler):
    STREAM_DELAY = 0.0  # seconds between chunks, to simulate slow generation

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "mock-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        chat = self.path.endswith("/chat/completions")
        if chat:
            msgs = payload["messages"]
            system = next((m["content"] for m in msgs if m["role"] == "system"), "")
            user = next((m["content"] for m in msgs if m["role"] == "user"), "")
        else:
            system, user = "", payload.get("prompt", "")
        text = pick_response(system, user)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        blob = (system + "\n" + user).lower()
        if chat and "cutoff-test" in blob:
            # emulate output truncated by max_tokens (finish_reason=length)
            obj = {"choices": [{"delta": {"content": "This sentence stops mid"}}]}
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            obj = {"choices": [{"delta": {}, "finish_reason": "length"}]}
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        if chat and "nothink-test" in blob:
            # emulates Qwen stuck in a thinking loop: only answers directly
            # when the /no_think mitigation is in the prompt
            if "/no_think" in blob:
                for piece in ("Direct ", "answer."):
                    obj = {"choices": [{"delta": {"content": piece}}]}
                    self.wfile.write(
                        f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            else:
                for piece in ("looping ", "thoughts " * 30):
                    obj = {"choices": [{"delta": {"reasoning": piece}}]}
                    self.wfile.write(
                        f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        if chat and "retry-think-test" in blob:
            # thinks everything away at a small budget; succeeds at a big one
            if payload.get("max_tokens", 0) >= 2048:
                for piece in ("Recovered ", "summary."):
                    obj = {"choices": [{"delta": {"content": piece}}]}
                    self.wfile.write(
                        f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            else:
                for piece in ("endless ", "pondering " * 40):
                    obj = {"choices": [{"delta": {"reasoning": piece}}]}
                    self.wfile.write(
                        f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        if chat and "reasoning-test" in blob:
            # emulate a thinking model (Ollama/llama.cpp style): reasoning
            # arrives in a separate delta field before any content
            for piece in ("Deeply ", "pondering ", "the plot..."):
                obj = {"choices": [{"delta": {"reasoning": piece}}]}
                self.wfile.write(
                    f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
            if "only-think" not in blob:
                for piece in ("Clean ", "answer."):
                    obj = {"choices": [{"delta": {"content": piece}}]}
                    self.wfile.write(
                        f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        # stream in small chunks like a real server
        words = text.split(" ")
        for i in range(0, len(words), 8):
            piece = " ".join(words[i:i + 8])
            if i:
                piece = " " + piece
            if chat:
                obj = {"choices": [{"delta": {"content": piece}}]}
            else:
                obj = {"choices": [{"text": piece}]}
            # raw UTF-8 without \u escapes and without a charset header,
            # exactly like llama.cpp / koboldcpp / ollama stream it
            self.wfile.write(
                f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            if self.STREAM_DELAY:
                time.sleep(self.STREAM_DELAY)
        self.wfile.write(b"data: [DONE]\n\n")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    cfg = SectionConfig(base_url=f"http://127.0.0.1:{port}")
    import backends
    print(backends.test_connection(cfg))

    chunks = []
    board = pipeline.generate_storyboard(cfg, "a detective story", 3,
                                         on_chunk=chunks.append)
    assert "Blackwood" in board and len(chunks) > 3, "storyboard streaming failed"
    print(f"storyboard OK ({len(chunks)} streamed chunks)")

    raw, scenes = pipeline.generate_outline(cfg, board, 3)
    assert len(scenes) == 3 and scenes[2].title == "The Confession", scenes
    print("outline OK (3 scenes parsed)")

    story = StoryProject(name="mock-story", storyboard_text=board, scenes=scenes,
                         num_scenes=3)
    text = pipeline.generate_scene(cfg, story, 0, [])
    assert "library smelled" in text
    story.scenes[0].text = text
    story.scenes[0].summary = pipeline.generate_summary(cfg, text)
    assert "forced window" in story.scenes[0].summary
    # UTF-8 must survive the SSE stream: å/ä/ö and the em-dash intact
    assert "kvällstid" in story.scenes[0].summary, story.scenes[0].summary
    assert "Händelseförlopp" in story.scenes[0].summary
    assert "—" in story.scenes[0].summary and "Ã" not in story.scenes[0].summary
    print("scene + summary OK (UTF-8 å/ä/ö/— intact)")

    # scene 2 must include scene 1 continuity (prev tail; no summary of k-1 needed)
    system, user = pipeline.build_scene_prompts(cfg, story, 1, [])
    assert "library smelled" in user, "previous scene tail missing in scene 2 prompt"
    print("continuity context OK")

    # manual template mode via /v1/completions
    cfg.template_mode = TEMPLATE_MODE_MANUAL
    cfg.template_name = "ChatML"
    board2 = pipeline.generate_storyboard(cfg, "a detective story", 3)
    assert "Blackwood" in board2
    print("manual chat-template mode OK")

    # thinking models: separate reasoning deltas are shown live but excluded
    import backends
    cfg.template_mode = "auto"
    live = []
    out = backends.stream_generate(cfg, "sys", "REASONING-TEST",
                                   on_chunk=live.append)
    assert out == "Clean answer.", repr(out)
    assert any("pondering" in c for c in live), "reasoning not streamed live"
    # inline <think> blocks (koboldcpp style) are stripped from the result
    out2 = backends.stream_generate(cfg, "sys", "INLINE-THINK-TEST")
    assert out2 == "The real story begins here.", repr(out2)
    # a model that ONLY thinks must raise a clear error, not return nothing
    try:
        backends.stream_generate(cfg, "sys", "REASONING-TEST ONLY-THINK")
        raise AssertionError("expected BackendError for reasoning-only output")
    except backends.BackendError as e:
        assert "hidden" in str(e) and "Max tokens" in str(e), str(e)
    print("thinking-model handling OK (reasoning shown live, stripped from text)")

    # thinking retry: summary succeeds on the automatic second attempt
    small = SectionConfig(base_url=f"http://127.0.0.1:{port}")
    small.params.max_tokens = 512
    out3 = pipeline.generate_summary(small, "scene text RETRY-THINK-TEST")
    assert out3 == "Recovered summary.", repr(out3)
    # …and the bigger budget is remembered: the next call succeeds directly
    assert pipeline._THINKING_FLOOR.get("summary", 0) >= 2048
    small2 = SectionConfig(base_url=f"http://127.0.0.1:{port}")
    small2.params.max_tokens = 512
    out3b = pipeline.generate_summary(small2, "scene text RETRY-THINK-TEST")
    assert out3b == "Recovered summary.", repr(out3b)
    # persistent reasoning-only: summary falls back to an excerpt, no crash,
    # and the user is warned via the status notification
    warns = []
    pipeline.NOTIFY = warns.append
    try:
        out4 = pipeline.generate_summary(small, "scene text REASONING-TEST ONLY-THINK")
    finally:
        pipeline.NOTIFY = None
    assert "automatic excerpt" in out4, repr(out4)
    assert warns and "Re-summarize" in warns[0], warns
    # thinking LOOP (Qwen at low temperature): fixed by the /no_think +
    # temperature mitigation on retry, then remembered for the session
    pipeline.reset_thinking_state()
    loopy = SectionConfig(base_url=f"http://127.0.0.1:{port}")
    loopy.params.max_tokens = 512
    loopy.params.ranges["temperature"] = [0.3, 0.3]
    out_loop = pipeline.generate_summary(loopy, "scene text NOTHINK-TEST")
    assert out_loop == "Direct answer.", repr(out_loop)
    assert "summary" in pipeline._THINKING_MITIGATE
    # next call succeeds on the FIRST attempt (mitigation applied up front)
    loopy2 = SectionConfig(base_url=f"http://127.0.0.1:{port}")
    loopy2.params.max_tokens = 512
    loopy2.params.ranges["temperature"] = [0.3, 0.3]
    out_loop2 = pipeline.generate_summary(loopy2, "scene text NOTHINK-TEST")
    assert out_loop2 == "Direct answer.", repr(out_loop2)
    print("thinking-loop mitigation OK (/no_think + temperature, remembered)")

    # a model that only ever thinks is marked hopeless after the full-context
    # try, so later calls fail fast instead of burning minutes per scene
    pipeline.reset_thinking_state()
    hopeless = SectionConfig(base_url=f"http://127.0.0.1:{port}")
    hopeless.params.max_tokens = 512
    pipeline.NOTIFY = None
    out_h = pipeline.generate_summary(hopeless, "text REASONING-TEST ONLY-THINK")
    assert "automatic excerpt" in out_h, repr(out_h)
    assert any(k[0] == "summary" for k in pipeline._THINKING_HOPELESS), \
        "model was not marked hopeless"
    t_fast = time.time()
    out_h2 = pipeline.generate_summary(hopeless, "text REASONING-TEST ONLY-THINK")
    assert "automatic excerpt" in out_h2
    assert time.time() - t_fast < 2.0, "hopeless model was retried again"
    pipeline._THINKING_HOPELESS.clear()
    print("hopeless-model fast-path OK (no repeated retries)")

    # …but a scene that stays reasoning-only still raises (prose is essential)
    story_t = StoryProject(name="t", storyboard_text="# Title\nT\n",
                           scenes=[Scene(title="One",
                                         beat="REASONING-TEST ONLY-THINK")])
    try:
        pipeline.generate_scene(small, story_t, 0, [])
        raise AssertionError("expected ReasoningOnlyError for the scene")
    except backends.ReasoningOnlyError:
        pass
    print("thinking retry + summary fallback OK")

    # truncated output (finish_reason=length) is detected and logged
    import applog
    notes = []
    pipeline.NOTIFY = notes.append
    try:
        out5 = pipeline.generate_storyboard(
            SectionConfig(base_url=f"http://127.0.0.1:{port}"),
            "a story CUTOFF-TEST", 3)
        assert out5 == "This sentence stops mid", repr(out5)
        assert notes and "CUT OFF by Max tokens" in notes[0], notes
        assert "CUT OFF by Max tokens" in applog.read_log()
        # a normal completed call must NOT warn
        notes.clear()
        pipeline.generate_summary(cfg, "normal scene text")
        assert not notes, f"false cutoff warning: {notes}"
    finally:
        pipeline.NOTIFY = None
    print("max-tokens cutoff detection OK")

    # automatic character tracking: people are kept, headings are filtered
    cast = pipeline.generate_cast_update(cfg, "scene prose here", {})
    assert set(cast) == {"Inspector Hale", "Lady Blackwood"}, cast
    assert "Beginning" not in cast, "a heading was recorded as a character"
    assert "detective" in cast["Inspector Hale"]["desc"]
    assert cast["Inspector Hale"]["pronouns"] == "he/him", cast["Inspector Hale"]
    assert cast["Lady Blackwood"]["pronouns"] == "she/her"
    print("automatic character tracking OK (with pronouns)")

    # ollama without a model must fail with a clear message, not a server 400
    import backends
    ollama_cfg = SectionConfig(backend="ollama", model="",
                               base_url=f"http://127.0.0.1:{port}")
    try:
        pipeline.generate_storyboard(ollama_cfg, "x", 3)
        raise AssertionError("expected BackendError for ollama without model")
    except backends.BackendError as e:
        assert "Ollama requires a model" in str(e), str(e)
    print("ollama missing-model guard OK")

    server.shutdown()
    print("ALL MOCK-LLM TESTS PASSED")


if __name__ == "__main__":
    main()
