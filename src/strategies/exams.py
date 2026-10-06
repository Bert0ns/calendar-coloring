from typing import Any

from colors import Colors

from .base import PersistentColoringStrategy


class ExamColoringStrategy(PersistentColoringStrategy):
    """
    Strategy specifically for coloring Polimi exams.
    """

    COLOR_GREY = "8"
    COLOR_RED = "11"

    def __init__(self, interactive: bool = False) -> None:
        super().__init__("exam_states.json", interactive)
        self.subscribed_titles: set[str] = self._derive_subscribed_titles()
        self.prompted_keys: set[str] = set()
        # Decisions made interactively this run, keyed by exam title so the
        # user is asked only once per exam even with multiple dates.
        self.title_decisions: dict[str, dict[str, Any]] = {}

    def _derive_subscribed_titles(self) -> set[str]:
        subscribed = set()
        for key, state_data in self.state.items():
            if isinstance(state_data, dict) and state_data.get("subscribed", False):
                title = key.rsplit(" (", 1)[0]
                subscribed.add(title)
        return subscribed

    def determine_color(self, event: dict[str, Any]) -> str | None:
        title = event.get("summary", "")
        description = event.get("description", "")
        categories = event.get("categories", [])

        if not title.startswith("Esame: ") and "Esame" not in categories:
            return None

        start_info = event.get("start", {})
        date_str = start_info.get("dateTime", start_info.get("date", "Unknown Date"))
        if "T" in date_str:
            date_str = date_str.split("T")[0]

        cache_key = f"{title} ({date_str})"

        if self.interactive:
            # If already prompted in this run, return saved color
            if cache_key in self.prompted_keys and cache_key in self.state:
                return self.state[cache_key]["color"]
            self.prompted_keys.add(cache_key)

            existing_entry = self.state.get(cache_key)

            if cache_key not in self.state and title in self.title_decisions:
                # Reuse this run's decision for another date of the same exam
                # instead of asking again.
                decision = self.title_decisions[title]
                self.state[cache_key] = {
                    "color": decision["color"],
                    "subscribed": decision["subscribed"],
                }
                if decision["subscribed"]:
                    self.subscribed_titles.add(title)
                self._save_state()
                print(
                    f"{Colors.OKBLUE} ↳ Reusing '{title}' decision for {date_str} "
                    f"(color {decision['color']}).{Colors.ENDC}"
                )
                return decision["color"]

            # Determine suggestion/default for subscription
            default_ans: str | None = None
            if existing_entry is not None:
                default_ans = "y" if existing_entry.get("subscribed") else "n"
            elif description.startswith("Iscritto"):
                default_ans = "y"
            elif (
                description.startswith("Non iscritto")
                or title in self.subscribed_titles
            ):
                default_ans = "n"

            if default_ans == "y":
                suggestion_str = (
                    f" [{Colors.BOLD}Y{Colors.ENDC}/{Colors.BOLD}n{Colors.ENDC}]"
                )
            elif default_ans == "n":
                suggestion_str = (
                    f" [{Colors.BOLD}y{Colors.ENDC}/{Colors.BOLD}N{Colors.ENDC}]"
                )
            else:
                suggestion_str = (
                    f" [{Colors.BOLD}y{Colors.ENDC}/{Colors.BOLD}n{Colors.ENDC}]"
                )

            if title in self.subscribed_titles and (
                not existing_entry or not existing_entry.get("subscribed")
            ):
                print(
                    f"{Colors.WARNING} ↳ Note: Already subscribed to another date for '{title}'.{Colors.ENDC}"
                )

            is_subscribed = False
            while True:
                prompt_msg = f"{Colors.OKCYAN}❓ Subscribed to '{title}' on {date_str}?{suggestion_str}: {Colors.ENDC}"
                ans = input(prompt_msg).strip().lower()

                if ans == "" and default_ans:
                    ans = default_ans

                if ans in ["y", "yes"]:
                    is_subscribed = True
                    break
                elif ans in ["n", "no"]:
                    is_subscribed = False
                    break
                else:
                    print(
                        f"{Colors.WARNING} ↳ Please answer 'y' or 'n' (or press Enter for default).{Colors.ENDC}"
                    )

            # Prompt 2: Color Choice
            if existing_entry and "color" in existing_entry:
                default_color = existing_entry["color"]
            else:
                default_color = self.COLOR_RED if is_subscribed else self.COLOR_GREY

            Colors.print_color_palette()

            chosen_color = None
            valid_colors = [str(i) for i in range(1, 12)]
            while True:
                prompt_msg = f"{Colors.OKCYAN}❓ Pick a color ID for this exam (1-11) [Default {default_color}]: {Colors.ENDC}"
                ans = input(prompt_msg).strip()

                if ans == "":
                    chosen_color = default_color
                    break
                elif ans in valid_colors:
                    chosen_color = ans
                    break
                else:
                    print(
                        f"{Colors.WARNING} ↳ Invalid choice. Please pick from 1 to 11.{Colors.ENDC}"
                    )

            self.state[cache_key] = {
                "color": chosen_color,
                "subscribed": is_subscribed,
            }
            if is_subscribed:
                self.subscribed_titles.add(title)
            self.title_decisions[title] = {
                "color": chosen_color,
                "subscribed": is_subscribed,
            }
            self._save_state()
            return chosen_color

        else:
            if cache_key in self.state:
                return self.state[cache_key]["color"]

            if title in self.subscribed_titles:
                self.state[cache_key] = {
                    "color": self.COLOR_GREY,
                    "subscribed": False,
                }
                self._save_state()
                return self.COLOR_GREY

            color = None
            is_sub = False
            if description.startswith("Non iscritto"):
                color = self.COLOR_GREY
                is_sub = False
            elif description.startswith("Iscritto"):
                color = self.COLOR_RED
                is_sub = True

            if color is not None:
                self.state[cache_key] = {
                    "color": color,
                    "subscribed": is_sub,
                }
                if is_sub:
                    self.subscribed_titles.add(title)
                self._save_state()

            return color
