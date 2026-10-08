"""Founder rules of 1 Oct 2026 on the operator kit: stylised styles only, said out loud; no real people.

The kit pauses before it sends a brief that asks for a photoreal look or names a
real person, shows the server's ``brief_needs_creator_choice`` notices when the
server raises them, and resends ``acknowledged_notices`` only after a 409 (an
older server answers 422 to the unknown field). Look frames and look notes that
ask for a photoreal look are warned about, never blocked.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation import stylised_only as so
from creation.harness import stages_gated
from fake_api import FakeApi

LIVE_ACTION = (
    "Make it look like a live-action K-drama: a florist and a cold heir in Seoul."
)
ACTOR = (
    "A slow-burn office romance. The lead looks like Park Seo-joon, all quiet charm."
)
IDOL = "A trainee falls for an idol like Jungkook from BTS while the agency watches."
ARCHETYPE = (
    "A K-pop idol type falls for a cold CEO, 90s rock star vibe, rain on neon streets."
)

STYLE_NOTICE = {
    "kind": "style_not_available",
    "message": (
        "Fictora draws in these styles: Clear Morning, Cold Gate, Golden Hour, Jade Ascent, Meadow Hour, "
        "Peach Bloom, Riot Ink, Velvet Sovereign. Realistic or live-action isn't available. Pick one to "
        "continue; Golden Hour is the closest to your idea."
    ),
    "asked_for": "live-action",
    "styles": [
        {"preset_id": "modern-romance-3", "label": "Golden Hour"},
        {"preset_id": "slice-of-life", "label": "Meadow Hour"},
    ],
    "suggested_preset_id": "modern-romance-3",
    "real_people": [],
    "original_characters": [],
}
PERSON_NOTICE = {
    "kind": "real_person_not_allowed",
    "message": (
        "We can't draw or voice real people, so Park Seo-joon won't be in your story. We'll write an "
        "original character with the same vibe instead, and you'll see them in the cast before anything "
        "is drawn. If Park Seo-joon is someone you made up, just continue."
    ),
    "asked_for": "looks like Park Seo-joon",
    "styles": [],
    "suggested_preset_id": None,
    "real_people": ["Park Seo-joon"],
    "original_characters": [],
}


def _envelope(*notices: dict[str, Any]) -> dict[str, Any]:
    return {
        "error": {
            "code": "brief_needs_creator_choice",
            "message": " ".join(n["message"] for n in notices),
            "details": {"notices": list(notices)},
        },
        "request_id": "req_1",
    }


# --- detection -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("brief", "phrase"),
    [
        (LIVE_ACTION, "live-action"),
        ("Photorealistic faces, shot on 35mm, like a real movie.", "Photorealistic"),
        ("Keep it realistic looking, not anime.", "realistic looking"),
        ("Hyper-realistic rain and real actors.", "Hyper-realistic"),
    ],
)
def test_a_photoreal_ask_is_found(brief: str, phrase: str) -> None:
    found = so.find_photoreal_request(brief)
    assert found is not None and phrase.casefold() in found.casefold()


@pytest.mark.parametrize(
    "brief",
    [
        ARCHETYPE,
        "Realistic dialogue about grief; a realistic story of two sisters.",
        "Cinematic lighting, soft film grain, manhwa ink linework.",
    ],
)
def test_a_stylised_or_story_realism_brief_is_not_a_photoreal_ask(brief: str) -> None:
    assert so.find_photoreal_request(brief) is None


def test_named_real_people_are_found_by_how_the_brief_uses_them() -> None:
    assert so.find_real_people(ACTOR) == ("Park Seo-joon",)
    assert so.find_real_people(IDOL) == ("Jungkook",)
    assert so.find_real_people("The villain is played by Lee Min-ho.") == (
        "Lee Min-ho",
    )
    assert so.find_real_people("Her voice sounds like Taylor Swift.") == (
        "Taylor Swift",
    )


@pytest.mark.parametrize(
    "brief",
    [
        ARCHETYPE,
        "Mina looks like her mother, and he looks like a ghost.",
        "A cold CEO who looks like he never sleeps.",
        "It looks like I lost him again; Mina looks like Mom did at her age.",
    ],
)
def test_archetypes_and_family_resemblance_are_not_real_people(brief: str) -> None:
    assert so.find_real_people(brief) == ()


def test_the_clean_archetype_brief_raises_no_notice() -> None:
    assert so.brief_notices(ARCHETYPE) == []


def test_local_notices_name_the_styles_and_the_closest_one() -> None:
    (notice,) = so.brief_notices(LIVE_ACTION)
    assert notice["kind"] == "style_not_available"
    assert notice["suggested_preset_id"] == "modern-romance-3"
    labels = [style["label"] for style in notice["styles"]]
    assert "Golden Hour" in labels and "Jade Ascent" in labels and len(labels) == 8
    assert "isn't available" in notice["message"]


# --- draft path ------------------------------------------------------------------------------


class _Run:
    """A run session whose draft POST answers with queued statuses."""

    prefix = "run"

    def __init__(self, answers: list[tuple[int, dict[str, Any]]]) -> None:
        self.answers = answers
        self.bodies: list[dict[str, Any]] = []
        self.keys: list[str | None] = []
        self.saved: dict[str, Any] = {}

    def post_optional(
        self, _path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]:
        self.bodies.append(dict(body))
        self.keys.append(idempotency_key)
        return self.answers.pop(0)

    def poll_job(self, job_id: str, **_k: Any) -> dict[str, Any]:
        return {"status": "completed", "job_id": job_id}

    def spine(self, spine_id: str) -> dict[str, Any]:
        return {"spine_id": spine_id}

    def save(self, name: str, payload: Any) -> None:
        self.saved[name] = payload

    def emit(self, *_a: Any, **_k: Any) -> None:
        return None


ACCEPTED = (202, {"plan_job_id": "job_plan_1", "spine_id": "spine_1"})


def _draft(
    run: _Run,
    prompt: str,
    *,
    accept: tuple[str, ...] = (),
    preset: str = "slice-of-life",
):
    return stages_gated.start_draft(  # type: ignore[arg-type]
        run, prompt=prompt, preset_id=preset, preset_version="1", accept_notices=accept
    )


def test_a_live_action_brief_pauses_before_anything_is_sent() -> None:
    run = _Run([ACCEPTED])

    with pytest.raises(so.BriefNoticePause) as stopped:
        _draft(run, LIVE_ACTION)

    assert run.bodies == [], "nothing was sent"
    text = str(stopped.value)
    assert "PAUSED" in text and "Golden Hour" in text and "modern-romance-3" in text
    assert "--accept-notice style_not_available" in text


def test_a_named_actor_pauses_and_names_them() -> None:
    run = _Run([ACCEPTED])

    with pytest.raises(so.BriefNoticePause, match="Park Seo-joon") as stopped:
        _draft(run, ACTOR)

    assert run.bodies == []
    assert "--accept-notice real_person_not_allowed" in str(stopped.value)


def test_the_clean_archetype_brief_drafts_untouched_with_no_acknowledgement_field() -> (
    None
):
    run = _Run([ACCEPTED])

    spine_id, _ = _draft(run, ARCHETYPE)

    assert spine_id == "spine_1"
    assert len(run.bodies) == 1 and "acknowledged_notices" not in run.bodies[0]
    assert run.bodies[0]["prompt"] == ARCHETYPE


def test_an_acknowledged_brief_is_sent_first_without_the_field() -> None:
    """An older server never raises the 409 and 422s the unknown field: never send it unprompted."""

    run = _Run([ACCEPTED])

    _draft(run, LIVE_ACTION, accept=("style_not_available",))

    assert len(run.bodies) == 1 and "acknowledged_notices" not in run.bodies[0]


def test_a_server_409_is_answered_with_the_acknowledged_kinds_and_the_same_key() -> (
    None
):
    run = _Run([(409, _envelope(STYLE_NOTICE)), ACCEPTED])

    spine_id, _ = _draft(
        run, LIVE_ACTION, accept=("style_not_available",), preset="modern-romance-3"
    )

    assert spine_id == "spine_1"
    assert "acknowledged_notices" not in run.bodies[0]
    assert run.bodies[1]["acknowledged_notices"] == ["style_not_available"]
    assert run.bodies[1]["art_style_preset_id"] == "modern-romance-3"
    assert run.keys[0] == run.keys[1]


def test_a_server_409_the_operator_has_not_acknowledged_pauses_with_the_servers_words() -> (
    None
):
    """The server caught what the kit's mirror missed: show its notice, send nothing more."""

    run = _Run([(409, _envelope(STYLE_NOTICE, PERSON_NOTICE))])

    with pytest.raises(so.BriefNoticePause) as stopped:
        _draft(run, "A quiet romance.", accept=("style_not_available",))

    text = str(stopped.value)
    assert len(run.bodies) == 1
    assert "We can't draw or voice real people" in text and "Park Seo-joon" in text
    assert "--accept-notice real_person_not_allowed" in text


def test_another_draft_refusal_still_stops_with_the_servers_code() -> None:
    run = _Run([(422, {"error": {"code": "invalid_request", "message": "bad band"}})])

    with pytest.raises(SystemExit, match="invalid_request"):
        _draft(run, ARCHETYPE)


# --- look frames and look notes: warned, never blocked ----------------------------------------


def test_a_photoreal_look_frame_is_warned_about_and_still_drawn(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/look-frame")] = {
        "image_url": "https://cdn.example/look-frame/1.png",
        "cached": False,
        "cost_usd": 0.3,
    }
    out = io.StringIO()

    path = ec.run_look_frame(
        desk,
        description="Photorealistic live-action night street, real actors.",
        out=out,
    )

    assert path.is_file(), "a warning, not a block"
    printed = out.getvalue()
    assert "WARNING" in printed and "stylised" in printed
    assert any(call[0] == "POST" for call in api.calls)


def test_a_stylised_look_frame_draws_without_a_warning(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/look-frame")] = {
        "image_url": "https://cdn.example/look-frame/1.png",
        "cached": False,
        "cost_usd": 0.3,
    }
    out = io.StringIO()

    ec.run_look_frame(
        desk,
        description="Manhwa ink linework, cinematic lighting, film grain.",
        out=out,
    )

    assert "WARNING" not in out.getvalue()


def test_a_photoreal_look_note_is_warned_about_and_still_added(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/look-notes")] = {}
    out = io.StringIO()

    ec.run_look_note(desk, add="make the faces photorealistic", out=out)

    assert "WARNING" in out.getvalue()
    assert any(
        call[0] == "POST" and call[1].endswith("/look-notes") for call in api.calls
    )


CAST_TABLE_BRIEF = """# Brief

## Cast

| Name | Role | Speaks? | Age |
| --- | --- | --- | --- |
| Noor Vatan | Noodle cook | Yes | 24 |
| Dez Okoye | Repo man | Yes | 38 |

## Arc (this episode)

Repossession, then the twist. Ends on Dez's face.
"""


def test_a_cast_name_in_the_brief_is_not_a_real_person() -> None:
    # Three Payments Late (5 Oct 2026): "Ends on Dez's face." paused the draft.
    assert so.find_real_people(CAST_TABLE_BRIEF) == ()
    assert so.brief_notices(CAST_TABLE_BRIEF) == []


def test_cast_names_come_from_a_table_or_a_list() -> None:
    assert {"Noor Vatan", "Dez Okoye"} <= set(so.brief_cast_names(CAST_TABLE_BRIEF))
    listed = "## Cast\n\n- **Mina Park** — the heir\n- Joon: her bodyguard\n\n## Set\n\nA roof.\n"
    assert {"Mina Park", "Joon"} <= set(so.brief_cast_names(listed))


def test_a_real_likeness_is_still_flagged_beside_a_cast() -> None:
    brief = (
        CAST_TABLE_BRIEF + "\nDez looks like Park Seo-joon. Ends on Jungkook's face.\n"
    )
    assert so.find_real_people(brief) == ("Park Seo-joon", "Jungkook")


def test_the_pause_prints_the_phrase_each_notice_matched() -> None:
    """L-20261005-16: the operator sees what in the brief set the notice off, not only the message."""

    text = so.pause_text([PERSON_NOTICE, STYLE_NOTICE], desk="D")
    assert "Matched in the brief: 'looks like Park Seo-joon'." in text
    assert "Matched in the brief: 'live-action'." in text


def test_a_notice_without_a_matched_phrase_prints_no_empty_line() -> None:
    text = so.pause_text([{**PERSON_NOTICE, "asked_for": ""}], desk="D")
    assert "Matched in the brief" not in text
