from routes.merger import MergeRejected, merge_route
from routes.model import RouteDraft
from routes.revisions import RevisionNotFound, RevisionStore
from routes.validation import RouteValidationError, validate_route


class RegistrationRejected(ValueError):
    pass


def register_route(store: RevisionStore, draft: RouteDraft, parent_revision: int | None = None) -> int:
    try:
        return store.append(draft, parent_revision)
    except RouteValidationError as error:
        raise RegistrationRejected(str(error)) from error
    except RevisionNotFound as error:
        raise RegistrationRejected('parent revision was not found for this station') from error


def merge_routes(store: RevisionStore, station: str, parent_revision: int,
                 left_revision: int, right_revision: int, *,
                 left_renames, right_renames, decisions) -> int:
    try:
        parent = store.get(station, parent_revision)
        left = store.get(station, left_revision)
        right = store.get(station, right_revision)
    except RevisionNotFound as error:
        raise RegistrationRejected('a requested revision was not found for this station') from error

    if left_revision == right_revision:
        raise RegistrationRejected('the two child revisions must be distinct')
    if left.parent_revision != parent_revision or right.parent_revision != parent_revision:
        raise RegistrationRejected('both child revisions must directly follow the parent revision')

    # Build and fully validate the merge before touching the store, so a
    # rejected request leaves neither a route nor provenance behind.
    try:
        merged, provenance = merge_route(
            parent, left, right, left_renames, right_renames, decisions,
        )
        validate_route(merged)
        return store.append_merge(merged, provenance)
    except (MergeRejected, RouteValidationError) as error:
        raise RegistrationRejected(str(error)) from error
