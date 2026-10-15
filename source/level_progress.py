"""Generation-scoped loading events; totals may be unknown."""
from dataclasses import dataclass

@dataclass(frozen=True)
class LoadProgress:
    generation: int
    phase: str
    completed: int
    total: int | None
    label: str
