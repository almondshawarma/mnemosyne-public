"""The render queue's Telegram check (scripts/render_queue_check.py): it speaks when the queue stops draining, once,
and stays quiet while clips are only waiting their turn."""

import os
import time
from pathlib import Path

import pytest

import render_queue_check as check

CRASH = """Traceback (most recent call last):
  File "/home/amans/helios/mnemosyne/scripts/render_runner.py", line 33, in <module>
    from render import docker_args
ModuleNotFoundError: No module named 'numpy'
"""


def put(path: Path, minutes_ago: float, text: str = "{}") -> Path:
    path.write_text(text, encoding="utf-8")
    t = time.time() - minutes_ago * 60
    os.utime(path, (t, t))
    return path


@pytest.fixture
def queue(tmp_path: Path) -> Path:
    return tmp_path


def test_a_runner_that_dies_on_import_is_reported_at_once(queue: Path) -> None:
    put(queue / "a-clip.json", 2)
    put(queue / "runner.log", 0.5, "2026-10-24T16:26:28 rendered x in 148 s\n" + CRASH)
    found = check.problems(queue, queue / "runner.log", time.time())
    assert len(found) == 1 and "crashes" in found[0] and "numpy" in found[0]


def test_clips_waiting_their_turn_behind_a_render_are_fine(queue: Path) -> None:
    put(queue / "first.working", 10)
    for i in range(4):
        put(queue / f"next-{i}.json", 45)
    put(queue / "runner.log", 0.5, CRASH)          # an old crash, still the log's last words while this renders
    assert check.problems(queue, queue / "runner.log", time.time()) == []


def test_a_clip_left_waiting_with_nothing_rendering_is_reported(queue: Path) -> None:
    put(queue / "lonely.json", check.WAIT_MIN + 5)
    found = check.problems(queue, queue / "runner.log", time.time())
    assert len(found) == 1 and "lonely" in found[0] and "nothing is rendering" in found[0]


def test_a_render_past_the_timeout_is_reported(queue: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put(queue / "stuck.working", 0)
    monkeypatch.setattr(check, "started", lambda p: time.time() - (check.WORKING_MIN + 1) * 60)
    found = check.problems(queue, queue / "runner.log", time.time())
    assert len(found) == 1 and "stuck" in found[0]


def test_a_render_is_timed_from_when_it_was_taken_not_when_it_was_submitted(queue: Path) -> None:
    put(queue / "just-taken.working", 45)        # submitted 45 minutes ago, renamed to .working just now
    assert check.problems(queue, queue / "runner.log", time.time()) == []


def test_an_empty_queue_with_an_old_crash_in_the_log_is_quiet(queue: Path) -> None:
    put(queue / "runner.log", 0.5, CRASH)
    assert check.problems(queue, queue / "runner.log", time.time()) == []


def test_it_messages_once_when_it_starts_and_once_when_it_clears(queue: Path, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(check, "QUEUE", queue)
    monkeypatch.setattr(check, "LOG", queue / "runner.log")
    monkeypatch.setattr(check, "STATE", tmp_path / "state" / "check.json")
    monkeypatch.setattr(check, "load_env", lambda path: None)
    monkeypatch.setattr(check, "telegram", sent.append)
    put(queue / "lonely.json", check.WAIT_MIN + 5)
    check.main()
    check.main()
    assert len(sent) == 1 and "stuck" in sent[0]
    (queue / "lonely.json").unlink()
    check.main()
    check.main()
    assert len(sent) == 2 and "moving again" in sent[1]
