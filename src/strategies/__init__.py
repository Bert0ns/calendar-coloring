from .base import (
    CompositeColoringStrategy,
    EventColoringStrategy,
    PersistentColoringStrategy,
)
from .deadlines import DeadlineColoringStrategy
from .exams import ExamColoringStrategy
from .lectures import LectureColoringStrategy

__all__ = [
    "CompositeColoringStrategy",
    "DeadlineColoringStrategy",
    "EventColoringStrategy",
    "ExamColoringStrategy",
    "LectureColoringStrategy",
    "PersistentColoringStrategy",
]
