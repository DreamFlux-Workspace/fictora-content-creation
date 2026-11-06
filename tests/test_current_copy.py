"""The copy to post is read from the server's show standing.

Show standing names each episode's ``current_copy`` and flags ``current_copy_unchecked``; a
server that sends neither falls back to its ``final``, else to a downloaded file only when it
provably comes from the cut the app plays.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from creation.captions import desk_has_finished_episodes, show_caption_style
from creation.cli_ops import main as ops_main
from creation.ops.floor import (
    QUEUE_FILENAME,
    QUEUE_READ_SECONDS,
    status_rows,
    write_queue,
)
from creation.post import current_copy
from creation.post.current_copy import CopyStatus
from creation.post.current_copy import show_copies as real_show_copies
from creation.post.episode_name import next_episode_path

BASE = "https://drama.example"
SHA = "1a2b3c4d5e6f" + "0" * 52
OLD_SHA = "9f8e7d6c5b4a" + "0" * 52
URL = "https://media.example/ep01-v3.mp4"
JOB = "job-playing"
CHANGED = "2026-11-05T10:00:00+00:00"


def _standing(monkeypatch: pytest.MonkeyPatch, **row: Any) -> list[httpx.Request]:
    """Answer show standing with one finished episode carrying ``row``'s fields."""

    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {"episode_ordinal": 1, "video": "finished", **row}
        return httpx.Response(200, json={"episodes": [body]})

    monkeypatch.setattr(
        current_copy,
        "show_copies",
        lambda desk, **_k: real_show_copies(
            desk,
            api=(BASE, {"Authorization": "Bearer t"}),
            transport=httpx.MockTransport(answer),
        ),
    )
    return seen


def _copy(version: int = 3, sha: str = SHA, url: str = URL) -> dict[str, Any]:
    return {
        "episode_id": "ep_01",
        "content_sha256": sha,
        "video_url": url,
        "copy_kind": "delivered",
        "source_sha256": "b" * 64,
        "version": version,
        "exported_at": "2026-11-06T10:00:00Z",
    }


def _downloaded(desk: Path, url: str = URL, **meta: Any) -> Path:
    path = next_episode_path(desk / "ep01" / "takes", desk, 1)
    path.write_bytes(b"server copy")
    path.with_suffix(".json").write_text(
        json.dumps({"video_url": url, **meta}), encoding="utf-8"
    )
    return path


def _every_take_kept(desk: Path) -> None:
    path = desk / "series.json"
    series = json.loads(path.read_text(encoding="utf-8"))
    for gate in ("look", "plates"):
        series[gate]["status"] = "approved"
    episode = series["episodes"][0]
    episode["script"]["status"] = "approved"
    for take in episode["takes"]:
        take["verdict"] = "use"
    path.write_text(json.dumps(series), encoding="utf-8")


def _row(desk: Path) -> dict[str, str]:
    return status_rows(desk, current_copy.show_copies(desk))[0]


def test_the_servers_current_copy_is_the_copy_to_post_with_no_approval(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _every_take_kept(post_desk)
    watched = _downloaded(post_desk)
    seen = _standing(
        monkeypatch,
        current_copy=_copy(),
        current_copy_unchecked=False,
        final={"export_sha256": OLD_SHA, "current": True},
    )

    assert ops_main(["status", "--desk", str(post_desk)]) == 0

    out, err = capsys.readouterr()
    label = f"v3, file 1a2b3c4d5e6f, {watched.name}"
    assert "waiting=done" in out and f"copy to post: {label}" in out
    assert "9f8e7d6c5b4a" not in out, "current_copy wins over the old final field"
    assert err == ""
    assert "| ep01 | done |" in (post_desk / QUEUE_FILENAME).read_text(encoding="utf-8")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/v1/spines/spine_test/standing")
    ], "status only reads; nothing is approved"


def test_a_copy_not_on_this_laptop_says_so(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _standing(monkeypatch, current_copy=_copy(), current_copy_unchecked=False)

    assert (
        _row(post_desk)["copy"]
        == "v3, file 1a2b3c4d5e6f, not on this laptop yet: run finish for this episode"
    )


def test_an_unchecked_copy_is_shown_with_a_plain_warning(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _every_take_kept(post_desk)
    _downloaded(post_desk)
    _standing(monkeypatch, current_copy=_copy(), current_copy_unchecked=True)

    assert ops_main(["status", "--desk", str(post_desk), "--json"]) == 0

    out, err = capsys.readouterr()
    assert json.loads(out)[0]["waiting"] == str(CopyStatus.UNCHECKED)
    warning = "the server could not check which video the app plays"
    assert warning in err and "Watch it before posting." in err
    queue = (post_desk / QUEUE_FILENAME).read_text(encoding="utf-8")
    assert warning in queue and "Open gates: 0." in queue


def test_a_new_server_with_no_copy_saved_waits_on_finish_and_ignores_the_desk(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _downloaded(post_desk)
    _standing(monkeypatch, current_copy_unchecked=False)

    row = _row(post_desk)

    assert (row["waiting"], row["copy"]) == (str(CopyStatus.WAITING), "")


def test_an_old_server_falls_back_to_its_current_final(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    watched = _downloaded(post_desk)
    _standing(
        monkeypatch,
        final={
            "export_sha256": SHA,
            "video_url": URL,
            "approved_at": "2026-11-05T10:00:00Z",
            "current": True,
        },
    )

    row = _row(post_desk)

    assert row["waiting"] == str(CopyStatus.DONE)
    assert row["copy"] == f"file 1a2b3c4d5e6f, {watched.name}"


def test_an_old_server_without_a_final_counts_a_file_proven_from_the_playing_cut(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    proven = _downloaded(
        post_desk, generation_job_id=JOB, done_at="2026-11-05T11:00:00+00:00"
    )
    _downloaded(post_desk, url="https://media.example/other.mp4")
    _standing(monkeypatch, video_job_id=JOB, played_cut_changed_at=CHANGED)

    row = _row(post_desk)

    assert (row["waiting"], row["copy"]) == (str(CopyStatus.DONE), proven.name)
    assert current_copy.copy_warnings(current_copy.show_copies(post_desk)) == []
    write_queue(post_desk)
    assert "Open gates: 0." in (post_desk / QUEUE_FILENAME).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("meta", "row"),
    [
        ({}, {"video_job_id": JOB, "played_cut_changed_at": CHANGED}),
        (
            {"generation_job_id": "job-older", "done_at": "2026-11-05T11:00:00+00:00"},
            {"video_job_id": JOB, "played_cut_changed_at": CHANGED},
        ),
        (
            {"generation_job_id": JOB, "done_at": "2026-11-05T09:00:00+00:00"},
            {"video_job_id": JOB, "played_cut_changed_at": CHANGED},
        ),
        (
            {"generation_job_id": JOB, "done_at": "2026-11-05T11:00:00+00:00"},
            {"video_job_id": JOB},
        ),
    ],
    ids=["no-record", "older-run", "cut-changed-since", "change-time-unknown"],
)
def test_an_old_server_never_counts_an_unproven_laptop_file_as_done(
    post_desk: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    meta: dict[str, Any],
    row: dict[str, Any],
) -> None:
    _every_take_kept(post_desk)
    _downloaded(post_desk, **meta)
    _standing(monkeypatch, **row)

    assert ops_main(["status", "--desk", str(post_desk), "--json"]) == 0

    out, err = capsys.readouterr()
    status = json.loads(out)[0]
    assert (status["waiting"], status["copy"]) == (str(CopyStatus.UNCONFIRMED), "")
    assert (
        status["waiting"].startswith("can't confirm")
        and "don't post yet" in status["waiting"]
    )
    assert "can't confirm the file on this laptop" in err
    queue = (post_desk / QUEUE_FILENAME).read_text(encoding="utf-8")
    assert "Open gates: 1." in queue and "| ep01 | done" not in queue


def test_an_old_server_with_nothing_on_the_desk_waits_on_finish(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _standing(monkeypatch)

    assert _row(post_desk)["waiting"] == str(CopyStatus.WAITING)


def test_an_old_servers_final_of_an_older_cut_is_never_the_copy_to_post(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _downloaded(post_desk)
    _standing(monkeypatch, final={"export_sha256": OLD_SHA, "current": False})

    row = _row(post_desk)

    assert (row["waiting"], row["copy"]) == (str(CopyStatus.WAITING), "")


def test_an_unreachable_server_says_not_checked_everywhere_and_never_done(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _every_take_kept(post_desk)
    _downloaded(post_desk)
    monkeypatch.setattr(
        current_copy,
        "show_copies",
        lambda desk, **_k: real_show_copies(
            desk,
            api=(BASE, {}),
            transport=httpx.MockTransport(lambda _r: httpx.Response(401, json={})),
        ),
    )

    assert ops_main(["status", "--desk", str(post_desk), "--json"]) == 0

    out, err = capsys.readouterr()
    waiting = "not checked: the kit's key was refused"
    assert json.loads(out)[0]["waiting"] == waiting
    assert (
        "Not checked: could not read the copies to post from the server "
        "(the kit's key was refused)" in err
    )
    queue = (post_desk / QUEUE_FILENAME).read_text(encoding="utf-8")
    assert f"| ep01 | {waiting} |" in queue and "Open gates: 1." in queue


def test_captions_keep_the_continuing_style_when_the_server_cannot_be_read(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert desk_has_finished_episodes(post_desk) is False
    monkeypatch.setattr(
        current_copy,
        "show_copies",
        lambda desk, **_k: current_copy.not_checked("the server didn't answer"),
    )

    assert desk_has_finished_episodes(post_desk) is None
    assert show_caption_style(post_desk) == ("subtle", "unchecked")


def test_a_new_show_whose_copies_cannot_be_checked_saves_no_caption_style(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.caption_preview import caption_style_at_look
    from creation.production_config import load_production_config

    monkeypatch.setattr(
        current_copy,
        "show_copies",
        lambda desk, **_k: current_copy.not_checked("the server didn't answer"),
    )

    assert caption_style_at_look(post_desk, out=sys.stdout) is None

    assert not load_production_config(post_desk).caption_style
    out = capsys.readouterr().out
    assert "asked again at the next step" in out and "keeps Subtle" not in out


def test_captions_count_a_copy_to_post_as_a_finished_episode(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _standing(monkeypatch, current_copy=_copy(), current_copy_unchecked=False)

    assert desk_has_finished_episodes(post_desk) is True


def test_captions_count_an_unconfirmed_laptop_copy_as_a_finished_episode(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _downloaded(post_desk)
    _standing(monkeypatch, video_job_id=JOB, played_cut_changed_at=CHANGED)

    assert desk_has_finished_episodes(post_desk) is True


def test_a_gate_write_reads_the_copies_from_the_server(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _standing(monkeypatch, current_copy=_copy(), current_copy_unchecked=False)

    write_queue(post_desk)

    assert "| ep01 | done |" in (post_desk / QUEUE_FILENAME).read_text(encoding="utf-8")


def _loaders(
    monkeypatch: pytest.MonkeyPatch, *, env: str = "", full: Any = None
) -> None:
    from creation.harness import credentials

    monkeypatch.setattr(
        credentials, "env_drama_api_credentials", lambda *_a, **_k: (BASE, env)
    )

    def loader(*_a: object, **_k: object) -> tuple[str, str]:
        if isinstance(full, BaseException):
            raise full
        if full is None:
            raise AssertionError("the full loader (Railway CLI) must not be used")
        return BASE, full

    monkeypatch.setattr(credentials, "load_drama_api_credentials", loader)


@pytest.mark.parametrize(
    "failure",
    [
        SystemExit("FICTORA_DRAMA_GENERATION_SERVICE_TOKEN must be set"),
        FileNotFoundError("railway"),
        json.JSONDecodeError("bad", "{", 0),
    ],
)
def test_a_kit_token_that_cannot_be_had_reads_as_no_kit_key(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    _loaders(monkeypatch, full=failure)

    assert real_show_copies(post_desk) == current_copy.not_checked("the kit has no key")


def test_the_copies_use_the_kits_own_credentials(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"episodes": []})

    _loaders(monkeypatch, full="kit")

    copies = real_show_copies(post_desk, transport=httpx.MockTransport(answer))

    assert copies.checked and seen[0].headers["Authorization"] == "Bearer kit"


def test_a_queue_read_uses_only_the_env_token_never_railway(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaders(monkeypatch, env="")

    assert real_show_copies(post_desk, env_token_only=True) == current_copy.not_checked(
        "the kit has no key"
    )


def test_the_railway_cli_has_a_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from creation.harness import credentials

    monkeypatch.setattr(
        credentials, "env_drama_api_credentials", lambda *_a, **_k: (BASE, "")
    )
    limits: list[object] = []

    def slow(*_a: object, **kw: object) -> None:
        limits.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired("railway", kw.get("timeout") or 0)

    monkeypatch.setattr(credentials.subprocess, "run", slow)

    with pytest.raises(SystemExit):
        credentials.load_drama_api_credentials(Path("."))
    assert limits == [credentials.RAILWAY_TIMEOUT_SECONDS]


def _offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down", request=request)


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (lambda _r: httpx.Response(403, json={}), "the kit's key was refused"),
        (
            lambda _r: httpx.Response(404, json={"detail": "Not Found"}),
            "the server doesn't know this story",
        ),
        (lambda _r: httpx.Response(502, json={}), "the server had a problem"),
        (_offline, "the server didn't answer"),
        (_timeout, "the server didn't answer"),
    ],
)
def test_a_failed_read_names_its_cause(
    post_desk: Path,
    answer: Callable[[httpx.Request], httpx.Response],
    reason: str,
) -> None:
    copies = real_show_copies(
        post_desk,
        api=(BASE, {"Authorization": "Bearer t"}),
        transport=httpx.MockTransport(answer),
    )

    assert copies == current_copy.not_checked(reason)
    status = current_copy.copy_status(copies, 1)
    assert str(status) == f"not checked: {reason}" and not status.counts_done


def test_a_gate_write_reads_the_copies_quickly_with_the_env_token_only(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def spy(desk: Path, **kw: Any) -> current_copy.ShowCopies:
        seen.append(kw)
        return current_copy.ShowCopies()

    monkeypatch.setattr(current_copy, "show_copies", spy)

    write_queue(post_desk)

    assert seen == [{"timeout": QUEUE_READ_SECONDS, "env_token_only": True}]


def test_a_new_desk_still_writes_the_post_gate_older_kits_need(
    post_desk: Path,
) -> None:
    from creation.ops.state import load_series, save_series

    save_series(post_desk, load_series(post_desk))

    episode = json.loads((post_desk / "series.json").read_text(encoding="utf-8"))[
        "episodes"
    ][0]
    assert episode["post"]["status"] == "pending"


def _old_downloads(desk: Path, count: int) -> list[Path]:
    return [
        _downloaded(desk, url=f"https://media.example/ep01-old-{n}.mp4")
        for n in range(count)
    ]


def test_old_laptop_copies_past_the_two_kept_get_a_nudge_and_are_never_deleted(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _every_take_kept(post_desk)
    old = _old_downloads(post_desk, 4)
    current = _downloaded(post_desk)
    _standing(monkeypatch, current_copy=_copy(), current_copy_unchecked=False)

    assert ops_main(["status", "--desk", str(post_desk)]) == 0

    _out, err = capsys.readouterr()
    assert "Tip: 2 old downloaded copies (0.0 MB) can go" in err
    assert old[0].name in err and old[1].name in err
    assert old[2].name not in err and old[3].name not in err and current.name not in err
    assert "to the Bin yourself" in err
    assert all(
        path.is_file() and path.with_suffix(".json").is_file()
        for path in [*old, current]
    )
    assert "Tip: 2 old downloaded copies" in (post_desk / QUEUE_FILENAME).read_text(
        encoding="utf-8"
    )


def test_the_current_copy_and_two_older_laptop_copies_get_no_nudge(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _old_downloads(post_desk, 2)
    _downloaded(post_desk)
    _standing(monkeypatch, current_copy=_copy(), current_copy_unchecked=False)

    copies = current_copy.show_copies(post_desk)

    assert copies.old_files == ()
    assert not any("Tip:" in line for line in current_copy.copy_warnings(copies))


def test_no_nudge_while_the_copy_to_post_is_unknown(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _old_downloads(post_desk, 5)
    _standing(monkeypatch, current_copy_unchecked=False)

    assert current_copy.show_copies(post_desk).old_files == ()


def test_no_nudge_when_the_server_cannot_be_read(post_desk: Path) -> None:
    _old_downloads(post_desk, 5)

    copies = real_show_copies(
        post_desk,
        api=(BASE, {"Authorization": "Bearer t"}),
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )

    assert copies.old_files == ()
