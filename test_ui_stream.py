"""Offscreen UI test: live streaming into tabs + batch runs + story list.

Run with:  python test_ui_stream.py
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from http.server import ThreadingHTTPServer

from PySide6.QtWidgets import QApplication

import project as prj
from test_mock_llm import Handler


def pump(qapp, main, max_seconds=30):
    """Process events until the background job finishes."""
    start = time.time()
    while main.is_busy():
        qapp.processEvents()
        time.sleep(0.01)
        if time.time() - start > max_seconds:
            raise TimeoutError("job did not finish")
    qapp.processEvents()


def main_test():
    Handler.STREAM_DELAY = 0.02
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    qapp = QApplication(sys.argv)
    import app as appmod
    # isolate the test from the user's real settings.json
    test_settings = prj.APP_DIR / "settings.test.json"
    if test_settings.exists():
        test_settings.unlink()
    appmod.SETTINGS_FILE = test_settings
    main = appmod.MainWindow()
    for b in main.state.backends_cfg.values():
        b.base_url = f"http://127.0.0.1:{port}"
    for cfg in main.state.sections.values():  # ignore the user's settings.json
        cfg.backend = "koboldcpp"
        cfg.model = "mock-model"

    before_boards = set(prj.list_storyboards())

    # --- single storyboard creation must stream into the Storyboard tab ------
    main.tab_start.concept_edit.setPlainText("a detective story")
    seen_during = []

    main.tab_start.create_storyboard()
    # capture editor content while the job is still running; midway, switch to
    # an old storyboard — its view must NOT be polluted by the live stream
    sb = main.tab_storyboard
    switched_ok = None
    t0 = time.time()
    while main.is_busy() and time.time() - t0 < 30:
        qapp.processEvents()
        text_now = sb.editor.toPlainText()
        if text_now and switched_ok is None:
            seen_during.append(len(text_now))
        if (switched_ok is None and len(seen_during) > 5
                and seen_during[-1] > seen_during[0]
                and sb.list_old.count() >= 1):
            sb.list_old.setCurrentRow(0)
            qapp.processEvents()
            old_text = sb.editor.toPlainText()
            polluted = False
            for _ in range(15):
                if not main.is_busy():
                    break  # done-handler may now legitimately switch the view
                qapp.processEvents()
                if main.is_busy() and sb.editor.toPlainText() != old_text:
                    polluted = True
                    break
                time.sleep(0.01)
            # the "⏵ generating…" entry must exist and bring back the live view
            if main.is_busy():
                assert sb.list.count() >= 1 and \
                    sb.list.item(0).text().startswith("⏵"), \
                    "no generating entry in the list during streaming"
                sb.list.setCurrentRow(0)
                qapp.processEvents()
                assert sb._viewing_stream, "clicking the entry did not return to live view"
                assert sb.editor.toPlainText() == sb._stream_buffer, \
                    "live view does not show the buffered stream"
            switched_ok = not polluted
        time.sleep(0.005)
    qapp.processEvents()
    assert seen_during and seen_during[0] < seen_during[-1], \
        f"storyboard did not stream live: {seen_during[:5]}"
    if switched_ok is not None:
        assert switched_ok, "live stream polluted the old storyboard view"
    # when done, the finished board is auto-selected and fully shown, and the
    # "⏵ generating…" entry is gone
    assert "Blackwood" in sb.editor.toPlainText()
    assert sb.list.count() >= 1, "storyboard list not refreshed"
    assert not any(sb.list.item(i).text().startswith("⏵")
                   for i in range(sb.list.count())), \
        "generating entry still in the list after completion"
    print(f"storyboard streams live OK ({len(seen_during)} UI updates, "
          f"mid-stream switch checked: {switched_ok})")

    # --- batch run: 2 stories, all-new mode ----------------------------------
    before_projects = set(prj.list_projects())
    main.tab_start.batch_spin.setValue(2)
    main.tab_start.mode_all_new.setChecked(True)

    # enable two of the (default-off) log detail options through the UI
    main.tab_log.opt_checks["summary_text"].setChecked(True)
    main.tab_log.opt_checks["prompts_scene"].setChecked(True)

    writer_saw_text = []
    summary_saw_text = []
    outline_saw_scenes = []
    outline_saw_stream = []
    switch_checked = False
    main.tab_start.run_batch()
    t0 = time.time()
    while main.is_busy() and time.time() - t0 < 60:
        qapp.processEvents()
        wt = main.tab_writer
        # mid-stream: switch away to another scene and back — the editor must
        # show the full buffered text, not start empty
        if (not switch_checked and wt._streaming_index is not None
                and len(wt._stream_buffer) > 40 and wt.list.count() >= 2):
            streaming_row = wt._streaming_index
            other_row = 0 if streaming_row != 0 else 1
            wt.list.setCurrentRow(other_row)
            qapp.processEvents()
            wt.list.setCurrentRow(streaming_row)
            qapp.processEvents()
            shown = wt.editor.toPlainText()
            assert shown == wt._stream_buffer and shown, \
                "switching back mid-stream lost the streamed text"
            switch_checked = True
        if main.tab_writer.editor.toPlainText():
            writer_saw_text.append(len(main.tab_writer.editor.toPlainText()))
        if wt._summarizing_index is not None and wt.summary_edit.toPlainText():
            summary_saw_text.append(len(wt.summary_edit.toPlainText()))
        if main.tab_outline.list.count():
            outline_saw_scenes.append(main.tab_outline.list.count())
        if main.tab_outline.raw_view.toPlainText():
            outline_saw_stream.append(len(main.tab_outline.raw_view.toPlainText()))
        time.sleep(0.005)
    qapp.processEvents()

    new_projects = set(prj.list_projects()) - before_projects
    assert len(new_projects) == 2, f"expected 2 new stories, got {new_projects}"
    assert writer_saw_text, "scene writer never showed streamed text during batch"
    assert outline_saw_scenes and outline_saw_scenes[-1] == 3, \
        "outline tab never showed the scenes during batch"
    assert outline_saw_stream and outline_saw_stream[0] < outline_saw_stream[-1], \
        "outline raw text did not stream live during batch"
    import re as _re
    for name in new_projects:
        assert _re.search(r"\d{4}-\d{2}-\d{2}_\d{4}", name), \
            f"project name has no date+time: {name}"
    # latest-run list holds this batch's storyboards, older list the rest
    assert main.tab_storyboard.list.count() == 2, \
        f"latest-run list: {main.tab_storyboard.list.count()} (expected 2)"
    assert main.tab_storyboard.list_old.count() == len(prj.list_storyboards()) - 2
    # params were rolled and recorded, and the log captured the calls
    story = main.state.project
    assert story is not None and "writer" in story.gen_info
    assert "temperature=" in story.gen_info["writer"]["params"]
    import applog
    log_text = applog.read_log()
    assert "[scene] request" in log_text and "summary text:" in log_text, \
        "log missing scene requests or summary texts"
    assert "[scene] SYSTEM PROMPT:" in log_text and "[scene] USER PROMPT:" in log_text, \
        "scene prompts not logged despite the option being on"
    assert "[storyboard] SYSTEM PROMPT:" not in log_text, \
        "storyboard prompts logged although that option is off"
    # the writer tab shows the selected scene's summary
    main.tab_writer.list.setCurrentRow(0)
    qapp.processEvents()
    assert "forced window" in main.tab_writer.summary_edit.toPlainText(), \
        "scene summary not shown in the writer tab"
    assert switch_checked, "mid-stream scene switch was never exercised"
    assert summary_saw_text, \
        "scene summaries never streamed into the summary box during the batch"
    # newest story first in the Complete Story list
    listed_now = prj.list_projects()
    assert listed_now and listed_now[0] in new_projects, \
        "newest story is not first in the project list"
    print(f"batch OK: 2 stories, writer streamed ({len(writer_saw_text)} updates), "
          f"outline streamed ({len(outline_saw_stream)} updates), lists split OK, "
          "dated filenames OK, gen_info + log OK, mid-stream switch OK, newest-first OK")

    # --- complete story tab lists all saved stories ---------------------------
    main.tab_complete.refresh_all()
    listed = [main.tab_complete.list.item(i).text()
              for i in range(main.tab_complete.list.count())]
    for name in new_projects:
        assert name in listed, f"{name} missing from Complete Story list"
    # selecting an older story shows its text
    main.tab_complete.list.setCurrentRow(0)
    qapp.processEvents()
    assert main.tab_complete.view.toPlainText().strip(), "story view empty"
    print(f"complete-story list OK ({len(listed)} stories listed)")

    # --- queue: soft stop + removal + sequential processing -----------------------
    before_q = set(prj.list_projects())
    main.tab_start.batch_spin.setValue(2)
    main.tab_start.run_batch()          # starts immediately (2 stories planned)
    main.tab_start.batch_spin.setValue(1)
    main.tab_start.run_batch()          # queued behind the running one
    main.tab_start.run_batch()          # queued (will be removed)
    qapp.processEvents()
    assert main.tab_start.current_spec is not None, "no batch running"
    assert len(main.state.job_queue) == 2, \
        f"expected 2 queued, got {len(main.state.job_queue)}"
    qt = main.tab_queue
    qt.refresh()
    assert qt.list.count() == 3, f"queue tab rows: {qt.list.count()}"
    qt.list.setCurrentRow(2)
    qt._remove_selected()
    assert len(main.state.job_queue) == 1, "queue item was not removed"

    # wait until story 1 of 2 is actually generating, then finish-story-skip-rest
    t0 = time.time()
    while time.time() - t0 < 30:
        qapp.processEvents()
        if (main.tab_start.current_story_progress == (1, 2)
                and main.tab_writer._stream_buffer):
            break
        time.sleep(0.005)
    qt.refresh()
    assert "story 1 of 2" in qt.list.item(0).text(), qt.list.item(0).text()
    main.tab_start.finish_story_skip_rest()
    qt.refresh()
    assert "skipping the rest" in qt.list.item(0).text()
    assert "story 1 of 1" in qt.list.item(0).text(), qt.list.item(0).text()
    assert "1 story(ies)" in qt.list.item(0).text(), qt.list.item(0).text()

    t0 = time.time()
    while (main.is_busy() or main.state.job_queue) and time.time() - t0 < 90:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    q_projects = set(prj.list_projects()) - before_q
    # 1 story from the soft-stopped 2-story batch + 1 from the queued batch
    assert len(q_projects) == 2, \
        f"expected 2 stories (soft-stop dropped one), got {len(q_projects)}"
    new_projects |= q_projects
    print("queue OK: progress shown, finish-story-skip-rest dropped the 2nd story, "
          "removal worked, next batch ran")

    # --- existing-storyboard source: batch skips board generation -----------------
    ts = main.tab_start
    ts.radio_board.setChecked(True)
    qapp.processEvents()
    assert not ts.mode_all_new.isEnabled(), "'all new' must be disabled"
    assert ts.mode_same_board.isChecked(), "'same storyboard' should be forced"
    assert ts.board_box.count() >= 1, "no storyboards listed"
    ts.board_box.setCurrentIndex(0)
    bname = ts.board_box.currentText()
    ts.board_preview.setPlainText(
        ts.board_preview.toPlainText() + "\n\nEDIT-MARK-BOARD")
    before_b = set(prj.list_projects())
    ts.batch_spin.setValue(1)
    ts.run_batch()
    t0 = time.time()
    while (main.is_busy() or main.state.job_queue) and time.time() - t0 < 60:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    new_b = set(prj.list_projects()) - before_b
    assert len(new_b) == 1, f"expected 1 story from the storyboard file, got {new_b}"
    from project import StoryProject as _SP
    story_b = _SP.load(prj.PROJECTS_DIR / f"{next(iter(new_b))}.json")
    assert story_b.storyboard_name == bname, "story not bound to the chosen board"
    assert "EDIT-MARK-BOARD" in story_b.storyboard_text, \
        "preview edits did not reach the generated story"
    assert "EDIT-MARK-BOARD" in prj.load_storyboard(bname), \
        "preview edits were not saved to the storyboard file"
    new_projects |= new_b
    ts.radio_text.setChecked(True)
    qapp.processEvents()
    assert ts.mode_all_new.isEnabled(), "'all new' should re-enable"
    print("existing-storyboard source OK (no board generation, edits flushed)")

    # --- theme toggle ------------------------------------------------------------
    from theme import apply_theme
    apply_theme("light")
    light = qapp.palette().window().color().lightness()
    apply_theme("dark")
    dark = qapp.palette().window().color().lightness()
    assert light > dark, "light theme is not lighter than dark theme"
    print("theme toggle OK")

    # cleanup test artifacts (projects and storyboards created by this test)
    for name in new_projects:
        (prj.PROJECTS_DIR / f"{name}.json").unlink(missing_ok=True)
    for name in set(prj.list_storyboards()) - before_boards:
        prj.delete_storyboard(name)
    main.state.project = None
    main.close()
    test_settings.unlink(missing_ok=True)
    server.shutdown()
    print("ALL UI STREAMING TESTS PASSED")


if __name__ == "__main__":
    main_test()
