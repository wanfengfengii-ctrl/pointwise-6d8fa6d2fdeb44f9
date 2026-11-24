"""Three-way merge of two fixed corrections sharing one parent revision."""
from collections.abc import Mapping
from dataclasses import dataclass

from routes.model import RouteAction, RouteDraft, RouteRevision


class MergeRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MergeProvenance:
    station: str
    revision: int
    parent: int
    left: int
    right: int
    # Ordered records ``(category, key, field, side)``, e.g.
    # ("cell", "scan", "actions", "right").
    decisions: tuple[tuple[str, str, str, str], ...]


def _mapping(value, label):
    if not isinstance(value, Mapping):
        raise MergeRejected(f'{label} must be a dictionary')
    return value


def _branch_identity(branch, parent_names, renames):
    """Map every workcell of one branch to its identity key.

    Parent identities keep the parent workcell name. Cells added by the
    branch receive a branch-qualified key, so two branches never share an
    identity merely because they reused a name.
    """
    renames = _mapping(renames, 'rename declaration')
    child_names = set(branch.draft.steps)
    claimed_child = {}
    claimed_parent = set()
    for parent_name, child_name in renames.items():
        if type(parent_name) is not str or type(child_name) is not str:
            raise MergeRejected('rename declaration keys and values must be names')
        if parent_name not in parent_names:
            raise MergeRejected(f'rename key {parent_name!r} is absent from the parent revision')
        if child_name not in child_names:
            raise MergeRejected(f'rename target {child_name!r} is absent from the branch revision')
        if parent_name == child_name:
            raise MergeRejected('a rename must use a different workcell name')
        if child_name in claimed_child:
            raise MergeRejected(f'rename target {child_name!r} is claimed more than once')
        claimed_child[child_name] = parent_name
        claimed_parent.add(parent_name)

    identity = {}
    for child_name in child_names:
        if child_name in claimed_child:
            identity[child_name] = ('parent', claimed_child[child_name])
        elif child_name in parent_names and child_name not in claimed_parent:
            identity[child_name] = ('parent', child_name)
        else:
            identity[child_name] = ('branch', child_name)
    return identity


def _action_signature(action, identity):
    target = action.target
    if action.kind == 'step':
        if not isinstance(target, str) or target not in identity:
            raise MergeRejected('a step points at a workcell without an identity')
        target = identity[target]
    return action.kind, action.probability, action.minutes, target


def _remap_action(action, name_by_identity, identity_by_cell):
    target = action.target
    if action.kind == 'step':
        target = name_by_identity[identity_by_cell[target]]
    return RouteAction(action.kind, action.probability, action.minutes, target)


class MergeEngine:
    def __init__(self, parent, left, right, left_renames, right_renames, decisions):
        self.parent = parent
        self.left = left
        self.right = right
        self.branches = {'left': left, 'right': right}
        self.decisions = _mapping(decisions, 'decisions')
        self.conflicts = set()
        self._validate_decision_keys()
        self.parent_draft = parent.draft
        self.parent_names = set(self.parent_draft.steps)
        self.identity = {
            'left': _branch_identity(left, self.parent_names, left_renames),
            'right': _branch_identity(right, self.parent_names, right_renames),
        }
        self.inverse = {
            side: {key: name for name, key in table.items()}
            for side, table in self.identity.items()
        }
        self.signatures = {
            side: {
                table[name]: tuple(_action_signature(action, table) for action in branch.draft.steps[name])
                for name in branch.draft.steps
            }
            for side, (branch, table) in (
                ('left', (left, self.identity['left'])),
                ('right', (right, self.identity['right'])),
            )
        }
        parent_identity = {name: ('parent', name) for name in self.parent_names}
        self.parent_signatures = {
            ('parent', name): tuple(
                _action_signature(action, parent_identity) for action in actions
            )
            for name, actions in self.parent_draft.steps.items()
        }

    def _validate_decision_keys(self):
        allowed = {('cell', 'name'), ('cell', 'actions'), ('cell', 'presence'), ('entry', 'target')}
        for key, side in self.decisions.items():
            if (not isinstance(key, tuple) or len(key) != 3
                    or key[0] not in ('cell', 'entry')
                    or not isinstance(key[1], str) or not isinstance(key[2], str)
                    or (key[0], key[2]) not in allowed):
                raise MergeRejected(f'illegal decision key {key!r}')
            if side not in ('left', 'right'):
                raise MergeRejected(f'illegal decision side {side!r} for conflict {key!r}')

    def _decide(self, key):
        if key not in self.decisions:
            raise MergeRejected(f'missing decision for conflict {key!r}')
        return self.decisions[key]

    def _workcells(self):
        # (name_side, action_side) per surviving identity.
        survivors = {}
        all_identities = set(self.identity['left'].values()) | set(self.identity['right'].values())
        for key in sorted(all_identities):
            in_left = key in self.inverse['left']
            in_right = key in self.inverse['right']

            if key[0] == 'branch':
                # Cells added on one branch keep that branch's own identity.
                if in_left and in_right:
                    raise MergeRejected('branch-added cells cannot share an identity')
                side = 'left' if in_left else 'right'
                survivors[key] = (side, side)
                continue

            parent_name = key[1]
            if not in_left or not in_right:
                present_side = 'left' if in_left else 'right'
                present_name = self.inverse[present_side][key]
                untouched = (
                    present_name == parent_name
                    and self.signatures[present_side][key] == self.parent_signatures[key]
                )
                if untouched:
                    # One branch deleted a cell the other left alone: delete.
                    continue
                decision_key = ('cell', parent_name, 'presence')
                self.conflicts.add(decision_key)
                side = self._decide(decision_key)
                if side != present_side:
                    # The chosen side is the deleting side: drop the whole cell.
                    continue
                survivors[key] = (present_side, present_side)
                continue

            # Both branches retain the parent cell: name and the full action
            # group are compared independently. A branch that kept the parent
            # name raises no conflict against the other branch's rename.
            left_name = self.inverse['left'][key]
            right_name = self.inverse['right'][key]
            name_side = action_side = None
            if left_name != right_name:
                if left_name == parent_name:
                    name_side = 'right'
                elif right_name == parent_name:
                    name_side = 'left'
                else:
                    decision_key = ('cell', parent_name, 'name')
                    self.conflicts.add(decision_key)
                    name_side = self._decide(decision_key)
            if self.signatures['left'][key] != self.signatures['right'][key]:
                left_touched = self.signatures['left'][key] != self.parent_signatures[key]
                right_touched = self.signatures['right'][key] != self.parent_signatures[key]
                if not left_touched:
                    action_side = 'right'
                elif not right_touched:
                    action_side = 'left'
                else:
                    decision_key = ('cell', parent_name, 'actions')
                    self.conflicts.add(decision_key)
                    action_side = self._decide(decision_key)
            if name_side is None:
                name_side = 'left'
            if action_side is None:
                action_side = 'left'
            survivors[key] = (name_side, action_side)
        return survivors

    def _entries(self, survivors, final_name):
        entry_targets = {'left': {}, 'right': {}}
        for side, branch in self.branches.items():
            for entry_name, cell_name in branch.draft.entries.items():
                entry_targets[side][entry_name] = self.identity[side][cell_name]

        merged_entries = {}
        parent_targets = {
            entry_name: self._parent_entry_target(entry_name)
            for entry_name in set(self.parent_draft.entries)
        }
        for entry_name in sorted(set(entry_targets['left']) | set(entry_targets['right'])):
            left_target = entry_targets['left'].get(entry_name)
            right_target = entry_targets['right'].get(entry_name)
            parent_target = parent_targets.get(entry_name)
            inherited = entry_name in self.parent_draft.entries

            if left_target == right_target:
                target = left_target
            elif not inherited and (left_target is None or right_target is None):
                # An entry added on only one branch has no parent baseline.
                target = left_target if right_target is None else right_target
            elif left_target is None or right_target is None:
                present_target = left_target if right_target is None else right_target
                if present_target == parent_target:
                    # One branch deleted an entry the other left untouched:
                    # the deletion merges directly.
                    target = None
                else:
                    # The surviving side also retargeted it, so the two sides
                    # genuinely diverge; choosing the missing side deletes it.
                    decision_key = ('entry', entry_name, 'target')
                    self.conflicts.add(decision_key)
                    side = self._decide(decision_key)
                    target = left_target if side == 'left' else right_target
            elif left_target == parent_target:
                target = right_target
            elif right_target == parent_target:
                target = left_target
            else:
                # Both sides retargeted the inherited entry differently.
                decision_key = ('entry', entry_name, 'target')
                self.conflicts.add(decision_key)
                side = self._decide(decision_key)
                target = left_target if side == 'left' else right_target
            if target is None:
                continue
            if target not in survivors:
                raise MergeRejected(f'entry {entry_name!r} dangles after the merge')
            merged_entries[entry_name] = final_name[target]
        return merged_entries

    def _parent_entry_target(self, entry_name):
        cell_name = self.parent_draft.entries.get(entry_name)
        if cell_name is None:
            return None
        return ('parent', cell_name)

    def build(self):
        survivors = self._workcells()

        final_name = {}
        for key, (name_side, _) in survivors.items():
            name = self.inverse[name_side][key]
            if name in final_name.values():
                raise MergeRejected(f'merged route contains duplicate workcell name {name!r}')
            final_name[key] = name

        merged_entries = self._entries(survivors, final_name)

        merged_steps = {}
        for key, (_, action_side) in survivors.items():
            source_name = self.inverse[action_side][key]
            identity = self.identity[action_side]
            actions = tuple(
                _remap_action(action, final_name, identity)
                for action in self.branches[action_side].draft.steps[source_name]
            )
            name = final_name[key]
            merged_steps[name] = actions

        # Every supplied decision must resolve a conflict that really occurred.
        extra = set(self.decisions) - self.conflicts
        if extra:
            raise MergeRejected(f'decision {sorted(extra)[0]!r} does not resolve any actual conflict')

        merged = RouteDraft(self.parent_draft.station, merged_entries, merged_steps)
        ordered_decisions = tuple(
            (key[0], key[1], key[2], self.decisions[key]) for key in sorted(self.conflicts)
        )
        provenance = MergeProvenance(
            self.parent_draft.station, None, self.parent.revision_id,
            self.left.revision_id, self.right.revision_id, ordered_decisions,
        )
        return merged, provenance


def merge_route(parent, left, right, left_renames, right_renames, decisions):
    if parent.draft.station != left.draft.station or parent.draft.station != right.draft.station:
        raise MergeRejected('all three revisions must belong to the requested station')
    engine = MergeEngine(parent, left, right, left_renames, right_renames, decisions)
    return engine.build()
