from collections.abc import Mapping, Sequence
from fractions import Fraction
from types import MappingProxyType

from routes.model import RouteAction, RouteDraft
from routes.port_map import build_port_map


class RouteValidationError(ValueError):
    pass


def _probability(value: Fraction | int | str) -> Fraction:
    if not isinstance(value, Fraction) and type(value) not in (int, str):
        raise RouteValidationError('probability must be an exact number')
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise RouteValidationError('invalid probability') from error
    if not 0 <= result <= 1:
        raise RouteValidationError('probability must be in [0, 1]')
    return result


def _validate_action(action: RouteAction, steps: Mapping) -> RouteAction:
    if not isinstance(action, RouteAction):
        raise RouteValidationError('expected a RouteAction')
    if type(action.minutes) is not int or action.minutes not in (0, 1):
        raise RouteValidationError('action minutes must be zero or one')
    if action.kind in ('step', 'handoff'):
        if not isinstance(action.target, str) or not action.target:
            raise RouteValidationError('action requires a named target')
        if action.kind == 'step' and action.target not in steps:
            raise RouteValidationError('unknown local workcell')
    elif action.kind not in ('release', 'hold') or action.target is not None:
        raise RouteValidationError('invalid action kind or unexpected target')
    return RouteAction(action.kind, _probability(action.probability), action.minutes, action.target)


def validate_route(draft: RouteDraft) -> RouteDraft:
    if not isinstance(draft, RouteDraft) or not isinstance(draft.station, str) or not draft.station:
        raise RouteValidationError('station name is required')
    if not isinstance(draft.steps, Mapping) or not draft.steps:
        raise RouteValidationError('at least one workcell is required')
    try:
        ports = build_port_map(draft)
    except ValueError as error:
        raise RouteValidationError(str(error)) from error
    steps = {}
    for name, actions in draft.steps.items():
        if not isinstance(name, str) or not name or not isinstance(actions, Sequence) or not actions:
            raise RouteValidationError('workcells need names and action sequences')
        checked = tuple(_validate_action(action, draft.steps) for action in actions)
        if sum(action.probability for action in checked) != 1:
            raise RouteValidationError(f'workcell {name!r} probability sum must equal one')
        steps[name] = checked
    return RouteDraft(draft.station, ports.entries, MappingProxyType(steps))
