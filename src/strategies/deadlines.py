import hashlib
from typing import Any, ClassVar

from colors import Colors

from .base import PersistentColoringStrategy


class DeadlineColoringStrategy(PersistentColoringStrategy):
    """
    Strategy specifically for coloring Polimi deadlines (e.g. thesis,
    internship and scholarship due dates).
    """

    PREFIX: ClassVar[str] = "Scadenza: "

    AVAILABLE_DEADLINE_COLORS: ClassVar[list[str]] = [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
    ]

    def __init__(self, interactive: bool = False) -> None:
        super().__init__("deadline_colors.json", interactive)
        self.prompted_deadlines: set[str] = set()

    def _get_deterministic_color(self, deadline_name: str) -> str:
        """
        Deterministically selects a color based on deadline name,
        independent of event processing order.
        """
        h = int(
            hashlib.sha256(deadline_name.strip().upper().encode("utf-8")).hexdigest(),
            16,
        )
        return self.AVAILABLE_DEADLINE_COLORS[h % len(self.AVAILABLE_DEADLINE_COLORS)]

    def determine_color(self, event: dict[str, Any]) -> str | None:
        title = event.get("summary", "")
        categories = event.get("categories", [])

        if title.startswith(self.PREFIX):
            deadline_name = title.removeprefix(self.PREFIX).strip()
        elif "Scadenza" in categories:
            # iCal feeds may omit the prefix; fall back to the category label
            deadline_name = title.strip()
        else:
            return None

        if self.interactive:
            if deadline_name in self.prompted_deadlines and deadline_name in self.state:
                return self.state[deadline_name]
            self.prompted_deadlines.add(deadline_name)

            default_color = (
                self.state[deadline_name]
                if deadline_name in self.state
                else self._get_deterministic_color(deadline_name)
            )

            status_text = (
                "Existing Deadline"
                if deadline_name in self.state
                else "New Deadline Detected"
            )
            print(
                f"\n{Colors.OKCYAN}⏰ {status_text}: {Colors.BOLD}'{deadline_name}'{Colors.ENDC}"
            )
            Colors.print_color_palette()

            while True:
                prompt_msg = f"{Colors.OKCYAN}❓ Pick a color ID for '{deadline_name}' (1-11) [Default {default_color}]: {Colors.ENDC}"
                ans = input(prompt_msg).strip()
                if ans == "":
                    chosen_color = default_color
                    break
                elif ans in self.AVAILABLE_DEADLINE_COLORS:
                    chosen_color = ans
                    break
                else:
                    print(
                        f"{Colors.WARNING} ↳ Invalid choice. Please pick from {self.AVAILABLE_DEADLINE_COLORS}.{Colors.ENDC}"
                    )

            self.state[deadline_name] = chosen_color
            self._save_state()
            return chosen_color

        else:
            if deadline_name not in self.state:
                self.state[deadline_name] = self._get_deterministic_color(deadline_name)
                self._save_state()

            return self.state[deadline_name]
