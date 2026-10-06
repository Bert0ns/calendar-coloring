"""Single source of truth for the Google Calendar event color palette."""

from __future__ import annotations

from enum import Enum


class GoogleColor(Enum):
    """The 11 event colors supported by Google Calendar, ordered by color ID."""

    LAVENDER = ("1", "Lavender", (121, 134, 203))
    SAGE = ("2", "Sage", (51, 182, 121))
    GRAPE = ("3", "Grape", (142, 36, 170))
    FLAMINGO = ("4", "Flamingo", (230, 124, 115))
    BANANA = ("5", "Banana", (246, 192, 38))
    TANGERINE = ("6", "Tangerine", (245, 81, 29))
    PEACOCK = ("7", "Peacock", (3, 155, 229))
    GRAPHITE = ("8", "Graphite", (97, 97, 97))
    BLUEBERRY = ("9", "Blueberry", (63, 81, 181))
    BASIL = ("10", "Basil", (11, 128, 67))
    TOMATO = ("11", "Tomato", (214, 0, 0))

    def __init__(self, color_id: str, label: str, rgb: tuple[int, int, int]) -> None:
        self.color_id = color_id
        self.label = label
        self.rgb = rgb

    @property
    def hex(self) -> str:
        return "#{:02x}{:02x}{:02x}".format(*self.rgb)

    @classmethod
    def from_id(cls, color_id: str) -> GoogleColor:
        """Returns the color with the given Google color ID (e.g. ``"11"``)."""
        for color in cls:
            if color.color_id == color_id:
                return color
        raise ValueError(f"Unknown Google Calendar color ID: {color_id!r}")

    @classmethod
    def parse(cls, color_id: object) -> GoogleColor | None:
        """Like :meth:`from_id`, but returns ``None`` for invalid input."""
        if not isinstance(color_id, str):
            return None
        try:
            return cls.from_id(color_id.strip())
        except ValueError:
            return None

    @classmethod
    def ids(cls) -> list[str]:
        return [color.color_id for color in cls]


EXAM_SUBSCRIBED_COLOR = GoogleColor.TOMATO
EXAM_NOT_SUBSCRIBED_COLOR = GoogleColor.GRAPHITE
