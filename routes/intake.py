from routes.model import RouteDraft
from routes.revisions import RevisionNotFound, RevisionStore
from routes.validation import RouteValidationError


class RegistrationRejected(ValueError):
    pass


def register_route(store: RevisionStore, draft: RouteDraft, parent_revision: int | None = None) -> int:
    try:
        return store.append(draft, parent_revision)
    except RouteValidationError as error:
        raise RegistrationRejected(str(error)) from error
    except RevisionNotFound as error:
        raise RegistrationRejected('parent revision was not found for this station') from error
