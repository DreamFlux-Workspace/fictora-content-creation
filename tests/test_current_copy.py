"""The copy to post is read from the server's show standing (fictora-drama #1407; 6 Nov 2026).

There is no "this episode is final" step. Show standing names each episode's ``current_copy``
and flags ``current_copy_unchecked``; a server without #1407 falls back to its old ``final``,
else to the newest file ``finish`` downloaded.
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


def _downloaded(desk: Path, url: str = URL) -> Path:
    """A file ``finish`` downloaded, with the link it came from beside it."""

    path = next_episode_path(desk / "ep01" / "takes", desk, 1)
    path.write_bytes(b"server copy")
    path.with_suffix(".json").write_text(
        json.dumps({"video_url": url}), encoding="utf-8"
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
    assert (
        "post" not in json.loads((post_desk / "series.json").read_text())["episodes"][0]
    )


def test_a_copy_not_on_this_laptop_says_so(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _standing(monkeypatch, current_copy=_copy(), current_copy_unchecked=False)

    assert _row(post_desk)["copy"] == "v3, file 1a2b3c4d5e6f, not on this laptop yet"


def test_an_unchecked_copy_is_shown_with_a_plain_warning(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _every_take_kept(post_desk)
    _downloaded(post_desk)
    _standing(monkeypatch, current_copy=_copy(), current_copy_unchecked=True)

    assert ops_main(["status", "--desk", str(post_desk), "--json"]) == 0

    out, err = capsys.readouterr()
    assert json.loads(out)[0]["waiting"] == CopyStatus.UNCHECKED
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

    assert (row["waiting"], row["copy"]) == (CopyStatus.WAITING, "")


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

    assert row["waiting"] == CopyStatus.DONE
    assert row["copy"] == f"file 1a2b3c4d5e6f, {watched.name}"


def test_an_old_server_without_a_final_falls_back_to_the_newest_desk_file(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    newest = _downloaded(post_desk)
    _standing(monkeypatch)

    row = _row(post_desk)
    copies = current_copy.show_copies(post_desk)

    assert (row["waiting"], row["copy"]) == (CopyStatus.UNCHECKED, newest.name)
    assert current_copy.copy_warnings(copies) == [
        "Warning: episode 1: this server cannot say which copy is the one to post yet, so it is "
        f"the newest file finished on this laptop ({newest.name}). Watch it before posting."
    ]


def test_an_old_server_with_nothing_on_the_desk_waits_on_finish(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _standing(monkeypatch)

    assert _row(post_desk)["waiting"] == CopyStatus.WAITING


def test_an_old_servers_final_of_an_older_cut_is_never_the_copy_to_post(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _every_take_kept(post_desk)
    _downloaded(post_desk)
    _standing(monkeypatch, final={"export_sha256": OLD_SHA, "current": False})

    row = _row(post_desk)

    assert (row["waiting"], row["copy"]) == (CopyStatus.WAITING, "")


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
    waiting = "finish (copy to post not checked: server said 401)"
    assert json.loads(out)[0]["waiting"] == waiting
    assert "Could not read the copies to post from the server (server said 401)" in err
    queue = (post_desk / QUEUE_FILENAME).read_text(encoding="utf-8")
    assert f"| ep01 | {waiting} |" in queue and "| ep01 | done |" not in queue


def test_captions_keep_the_continuing_style_when_the_server_cannot_be_read(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert desk_has_finished_episodes(post_desk) is False
    monkeypatch.setattr(
        current_copy,
        "show_copies",
        lambda desk, **_k: current_copy.not_checked("offline"),
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
        lambda desk, **_k: current_copy.not_checked("offline"),
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
def test_a_kit_token_that_cannot_be_had_reads_as_no_kit_token(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    _loaders(monkeypatch, full=failure)

    assert real_show_copies(post_desk) == current_copy.not_checked("no kit token")


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
        "no kit token"
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


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (
            lambda _r: httpx.Response(404, json={"detail": "Not Found"}),
            "server said 404",
        ),
        (_offline, "offline"),
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
    assert (
        current_copy.copy_status(copies, 1)
        == f"finish (copy to post not checked: {reason})"
    )


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
