from .base import (
    CompositeColoringStrategy,
    EventColoringStrategy,
    PersistentColoringStrategy,
)
from .exams import ExamColoringStrategy
from .lectures import LectureColoringStrategy

__all__ = [
    "CompositeColoringStrategy",
    "EventColoringStrategy",
    "ExamColoringStrategy",
    "LectureColoringStrategy",
    "PersistentColoringStrategy",
]
