from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from routes.model import RouteDraft


@dataclass(frozen=True, slots=True)
class PortMap:
    entries: Mapping[str, str]

    def resolve(self, name: str) -> str:
        return self.entries[name]


def build_port_map(draft: RouteDraft) -> PortMap:
    if not isinstance(draft.entries, Mapping) or not draft.entries:
        raise ValueError('at least one public entry is required')
    entries = {}
    for name, workcell in draft.entries.items():
        if not isinstance(name, str) or not name:
            raise ValueError('public entries need nonempty names')
        if not isinstance(workcell, str) or workcell not in draft.steps:
            raise ValueError(f'entry {name!r} refers to an unknown workcell')
        entries[name] = workcell
    return PortMap(MappingProxyType(entries))
