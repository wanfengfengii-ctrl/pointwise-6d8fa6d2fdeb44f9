from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class RouteAction:
    kind: str
    probability: Fraction | int | str
    minutes: int
    target: str | None = None


@dataclass(frozen=True, slots=True)
class RouteDraft:
    station: str
    entries: Mapping[str, str]
    steps: Mapping[str, Sequence[RouteAction]]


@dataclass(frozen=True, slots=True)
class RouteRevision:
    revision_id: int
    parent_revision: int | None
    draft: RouteDraft
