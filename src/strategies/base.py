import json
import os
from abc import ABC, abstractmethod
from typing import Any

from colors import Colors


class EventColoringStrategy(ABC):
    """Abstract base class defining the contract for coloring strategies."""

    @abstractmethod
    def determine_color(self, event: dict[str, Any]) -> str | None:
        """
        Returns the color ID for the event, or None if no change is needed.
        """


class CompositeColoringStrategy(EventColoringStrategy):
    """
    Evaluates multiple coloring strategies in order.
    Returns the first color determined by a strategy.
    """

    def __init__(self, strategies: list[EventColoringStrategy]) -> None:
        self.strategies = strategies

    def determine_color(self, event: dict[str, Any]) -> str | None:
        for strategy in self.strategies:
            color = strategy.determine_color(event)
            if color is not None:
                return color
        return None


class PersistentColoringStrategy(EventColoringStrategy):
    """Base class for strategies that persist state to JSON files."""

    def __init__(self, state_file: str, interactive: bool = False) -> None:
        self.state_file = state_file
        self.interactive = interactive
        # Always load existing state so interactive mode does not wipe saved data
        self.state: dict[str, Any] = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print(
                    f"{Colors.WARNING}⚠ Warning: '{self.state_file}' is corrupted. Starting fresh.{Colors.ENDC}"
                )
                return {}
        return {}

    def _save_state(self) -> None:
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=4)
