"""``look-frame`` against the fake API: the server draws the style frame, the desk saves it and never pins it."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.ops.state import episode_by_ordinal, load_series
from fake_api import FakeApi

ROUTE = "/v1/spines/sp1/look-frame"
URL = "https://cdn.example/tenants/abc/drama/operator-images/look-frame/0f0f.png"
DESCRIPTION = "Muted teal night, sodium streetlight, soft film grain, manhwa ink linework, rain on glass."
ANSWER = {
    "schema_version": "fictora.drama-look-frame-response.v1",
    "image_url": URL,
    "width": 1088,
    "height": 1936,
    "cached": False,
    "cost_usd": 0.3,
}
OLD_SERVER_404 = SystemExit(
    f'HTTP 404 POST https://api.example{ROUTE}: {{"detail": "Not Found"}}'
)


def _description(desk: Path, text: str = DESCRIPTION) -> Path:
    path = desk / "shared" / "look" / "look.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"\n{text}\n", encoding="utf-8")
    return path


def test_look_frame_draws_on_the_server_saves_the_png_books_one_still_and_never_pins(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ROUTE)] = ANSWER
    out = io.StringIO()

    path = ec.run_look_frame(desk, description=_description(desk), out=out)

    assert path == desk / "shared" / "look" / "look-frame-v1.png" and path.is_file()
    ((method, called, body, key),) = [call for call in api.calls if call[0] == "POST"]
    assert (method, called) == ("POST", ROUTE)
    assert body == {"description": DESCRIPTION, "size": "1088x1936"}
    assert key and key.startswith(f"{api.prefix}-look-frame-")
    assert not any("look-register" in call[1] for call in api.calls)
    assert ("download", {"url": URL, "path": str(path)}) in api.events
    series = load_series(desk)
    assert episode_by_ordinal(series, 1).spend_usd == pytest.approx(
        0.3
    ) and series.spend_usd == pytest.approx(0.3)
    printed = out.getvalue()
    assert (
        f"image_url: {URL}" in printed and f"look --desk {desk} --url {URL}" in printed
    )
    assert "0.3" not in printed and "$" not in printed
    saved = sorted((desk / "api").glob("look-frame-v*.json"))
    assert saved and json.loads(saved[-1].read_text())["image_url"] == URL


def test_the_same_description_keeps_its_key_and_a_cached_answer_books_nothing(
    desk: Path, api: FakeApi
) -> None:
    answers = [ANSWER, {**ANSWER, "cached": True, "cost_usd": 0}]
    api.routes[("POST", ROUTE)] = lambda *_: answers.pop(0)
    description = _description(desk)

    first = ec.run_look_frame(desk, description=description, out=io.StringIO())
    second = ec.run_look_frame(desk, description=description, out=io.StringIO())

    keys = [key for method, _path, _body, key in api.calls if method == "POST"]
    assert len(keys) == 2 and keys[0] == keys[1]
    assert first.name == "look-frame-v1.png" and second.name == "look-frame-v2.png"
    assert load_series(desk).spend_usd == pytest.approx(0.3)


def test_a_different_size_is_sent_and_changes_the_key(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", ROUTE)] = ANSWER
    description = _description(desk)

    ec.run_look_frame(desk, description=description, out=io.StringIO())
    ec.run_look_frame(
        desk, description=description, size="1936x1088", out=io.StringIO()
    )

    posts = [(body, key) for method, _path, body, key in api.calls if method == "POST"]
    assert posts[1][0] == {"description": DESCRIPTION, "size": "1936x1088"}
    assert posts[0][1] != posts[1][1]


def test_an_older_server_without_the_route_stops_with_a_clear_message_and_books_nothing(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ROUTE)] = OLD_SERVER_404

    with pytest.raises(ec.CommandStopped, match="no look-frame route yet") as caught:
        ec.run_look_frame(desk, description=_description(desk), out=io.StringIO())

    assert "never draw it with your own provider key" in str(caught.value)
    assert not (desk / "shared" / "look" / "look-frame-v1.png").exists()
    assert load_series(desk).spend_usd == 0


def test_a_missing_story_404_is_not_mistaken_for_an_older_server(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ROUTE)] = SystemExit(
        f"HTTP 404 POST https://api.example{ROUTE}: spine_not_found: Story spine is not available: sp1"
    )

    with pytest.raises(ec.CommandStopped) as caught:
        ec.run_look_frame(desk, description=_description(desk), out=io.StringIO())

    assert "spine_not_found" in str(caught.value) and "no look-frame route" not in str(
        caught.value
    )


def test_the_cli_exits_2_on_an_older_server(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("POST", ROUTE)] = OLD_SERVER_404

    code = produce_main(
        ["look-frame", "--desk", str(desk), "--description", str(_description(desk))]
    )

    assert code == 2 and "no look-frame route yet" in capsys.readouterr().err


def test_a_server_refusal_carries_its_hint(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", ROUTE)] = SystemExit(
        f"HTTP 422 POST https://api.example{ROUTE}: look_frame_text_only: A look frame is drawn from words only"
    )

    with pytest.raises(ec.CommandStopped, match="take the link out of the description"):
        ec.run_look_frame(
            desk,
            description=_description(desk, "like https://x.example/a.png"),
            out=io.StringIO(),
        )


@pytest.mark.parametrize(
    ("text", "size", "why"),
    [
        ("   \n", "1088x1936", "empty"),
        ("x" * 4001, "1088x1936", "4000"),
        (DESCRIPTION, "4096x4096", "--size"),
    ],
)
def test_bad_input_stops_before_any_call(
    desk: Path, api: FakeApi, text: str, size: str, why: str
) -> None:
    with pytest.raises(ec.CommandStopped, match=why):
        ec.run_look_frame(
            desk, description=_description(desk, text), size=size, out=io.StringIO()
        )
    assert not [call for call in api.calls if call[0] == "POST"]


def test_the_cli_runs_look_frame_then_look_pins_the_returned_url(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ROUTE)] = ANSWER
    api.routes[("POST", "/v1/spines/sp1/look-register")] = {"spine_id": "sp1"}

    assert (
        produce_main(
            [
                "look-frame",
                "--desk",
                str(desk),
                "--description",
                str(_description(desk)),
            ]
        )
        == 0
    )
    assert produce_main(["look", "--desk", str(desk), "--url", URL]) == 0

    assert api.posted("/v1/spines/sp1/look-register") == [
        {"spine_version": "v5", "url": URL}
    ]


def test_route_missing_reads_only_the_bare_not_found() -> None:
    assert ec.look_frame_route_missing(OLD_SERVER_404.code)  # type: ignore[arg-type]
    assert not ec.look_frame_route_missing(
        "HTTP 404 POST https://api.example/x: spine_not_found: gone"
    )
    assert not ec.look_frame_route_missing(
        'HTTP 409 POST https://api.example/x: {"detail": "Not Found"}'
    )


@pytest.mark.parametrize("form", ["inline", "at-file", "path"])
def test_the_cli_takes_the_description_as_words_at_file_or_a_path(
    desk: Path, api: FakeApi, form: str
) -> None:
    api.routes[("POST", ROUTE)] = ANSWER
    value = {
        "inline": DESCRIPTION,
        "at-file": f"@{_description(desk)}",
        "path": str(_description(desk)),
    }[form]

    assert (
        produce_main(["look-frame", "--desk", str(desk), "--description", value]) == 0
    )

    assert api.posted(ROUTE) == [{"description": DESCRIPTION, "size": "1088x1936"}]


def test_a_missing_at_file_stops_before_any_call(desk: Path, api: FakeApi) -> None:
    with pytest.raises(ec.CommandStopped, match="cannot read"):
        ec.run_look_frame(desk, description=f"@{desk / 'nope.txt'}", out=io.StringIO())
    assert not [call for call in api.calls if call[0] == "POST"]
