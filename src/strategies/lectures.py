import hashlib
from typing import Any, ClassVar

from colors import Colors

from .base import PersistentColoringStrategy


class LectureColoringStrategy(PersistentColoringStrategy):
    """
    Strategy specifically for coloring Polimi lectures.
    """

    AVAILABLE_LECTURE_COLORS: ClassVar[list[str]] = [
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
        super().__init__("course_colors.json", interactive)
        self.prompted_courses: set[str] = set()

    def _get_deterministic_color(self, course_name: str) -> str:
        """
        Deterministically selects a color based on course name,
        independent of event processing order.
        """
        h = int(
            hashlib.sha256(course_name.strip().upper().encode("utf-8")).hexdigest(), 16
        )
        return self.AVAILABLE_LECTURE_COLORS[h % len(self.AVAILABLE_LECTURE_COLORS)]

    def determine_color(self, event: dict[str, Any]) -> str | None:
        title = event.get("summary", "")

        if not title.startswith("Lezione: Didattica - "):
            return None

        course_name = title.replace("Lezione: Didattica - ", "").strip()

        if self.interactive:
            if course_name in self.prompted_courses and course_name in self.state:
                return self.state[course_name]
            self.prompted_courses.add(course_name)

            default_color = (
                self.state[course_name]
                if course_name in self.state
                else self._get_deterministic_color(course_name)
            )

            status_text = (
                "Existing Course"
                if course_name in self.state
                else "New Course Detected"
            )
            print(
                f"\n{Colors.OKCYAN}🎨 {status_text}: {Colors.BOLD}'{course_name}'{Colors.ENDC}"
            )
            Colors.print_color_palette()

            while True:
                prompt_msg = f"{Colors.OKCYAN}❓ Pick a color ID for ALL lectures of '{course_name}' (1-11) [Default {default_color}]: {Colors.ENDC}"
                ans = input(prompt_msg).strip()
                if ans == "":
                    chosen_color = default_color
                    break
                elif ans in self.AVAILABLE_LECTURE_COLORS:
                    chosen_color = ans
                    break
                else:
                    print(
                        f"{Colors.WARNING} ↳ Invalid choice. Please pick from {self.AVAILABLE_LECTURE_COLORS}.{Colors.ENDC}"
                    )

            self.state[course_name] = chosen_color
            self._save_state()
            return chosen_color

        else:
            if course_name not in self.state:
                self.state[course_name] = self._get_deterministic_color(course_name)
                self._save_state()

            return self.state[course_name]
