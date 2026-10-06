import pytest

from polimi_calendar_coloring.palette import (
    EXAM_NOT_SUBSCRIBED_COLOR,
    EXAM_SUBSCRIBED_COLOR,
    GoogleColor,
)


def test_palette_has_eleven_colors_ordered_by_id() -> None:
    assert GoogleColor.ids() == [str(i) for i in range(1, 12)]


@pytest.mark.parametrize("color", list(GoogleColor))
def test_from_id_round_trips(color: GoogleColor) -> None:
    assert GoogleColor.from_id(color.color_id) is color


@pytest.mark.parametrize("bad", ["0", "12", "", "red", " 1x"])
def test_from_id_rejects_unknown_ids(bad: str) -> None:
    with pytest.raises(ValueError):
        GoogleColor.from_id(bad)


@pytest.mark.parametrize("bad", [None, 3, "12", "", [], {"a": 1}])
def test_parse_returns_none_for_invalid_values(bad: object) -> None:
    assert GoogleColor.parse(bad) is None


def test_parse_tolerates_surrounding_whitespace() -> None:
    assert GoogleColor.parse(" 7 ") is GoogleColor.PEACOCK


def test_hex_and_labels() -> None:
    assert GoogleColor.TOMATO.hex == "#d60000"
    assert GoogleColor.LAVENDER.hex == "#7986cb"
    assert GoogleColor.BASIL.label == "Basil"


def test_exam_colors_match_legacy_ids() -> None:
    assert EXAM_SUBSCRIBED_COLOR.color_id == "11"
    assert EXAM_NOT_SUBSCRIBED_COLOR.color_id == "8"
