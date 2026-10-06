"""ANSI terminal styling."""

from __future__ import annotations

from polimi_calendar_coloring.palette import GoogleColor

BLUE = "\033[94m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def rgb(color: GoogleColor) -> str:
    r, g, b = color.rgb
    return f"\033[38;2;{r};{g};{b}m"


def style(text: str, *styles: str) -> str:
    return f"{''.join(styles)}{text}{RESET}"


def swatch(color: GoogleColor) -> str:
    return style(f"{color.color_id}: {color.label}", BOLD, rgb(color))


def palette_lines(columns: int = 4) -> list[str]:
    """The Google palette rendered with true colors, ``columns`` per line."""
    colors = list(GoogleColor)
    width = max(len(f"{c.color_id}: {c.label}") for c in colors) + 2
    lines = []
    for start in range(0, len(colors), columns):
        cells = []
        for color in colors[start : start + columns]:
            padding = " " * (width - len(f"{color.color_id}: {color.label}"))
            cells.append(swatch(color) + padding)
        lines.append("".join(cells).rstrip())
    return lines
