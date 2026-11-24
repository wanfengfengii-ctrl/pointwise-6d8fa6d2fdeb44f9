from dataclasses import dataclass

from exchange.package import PackageError
from exchange.pair import PairStore, StationPair
from reports.issue import issue_report
from reports.store import FinalReport, ReportStore
from review.conclusion import build_conclusion
from review.eventual import eventual_release
from review.network import ReleaseNetwork, compose_release_network
from review.turnaround import deadline_release


class RequestRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReportRequest:
    pair_id: int
    start_station: str
    start_entry: str
    deadline: int


def validate_request(pairs: PairStore, request: ReportRequest) -> tuple[StationPair, ReleaseNetwork, int]:
    if not isinstance(request, ReportRequest):
        raise RequestRejected('a report request is required')
    if type(request.deadline) is not int or request.deadline < 0:
        raise RequestRejected('deadline must be a nonnegative integer minute')
    if not all(isinstance(value, str) and value for value in (request.start_station, request.start_entry)):
        raise RequestRejected('a named station and start entry are required')
    try:
        pair = pairs.get(request.pair_id)
        network = compose_release_network(pair)
    except (KeyError, PackageError) as error:
        raise RequestRejected('selected pair or its evidence is unavailable or invalid') from error
    try:
        start = network.entries.index((request.start_station, request.start_entry))
    except ValueError as error:
        raise RequestRejected('start entry is not on the selected station piece') from error
    return pair, network, start


def report_turnaround(pairs: PairStore, reports: ReportStore, request: ReportRequest) -> FinalReport:
    pair, network, start = validate_request(pairs, request)
    try:
        by_deadline = deadline_release(network, start, request.deadline)
        eventual = eventual_release(network, start)
        result = build_conclusion(pair, request.start_station, request.start_entry, request.deadline, by_deadline, eventual)
    except ValueError as error:
        raise RequestRejected('selected evidence did not produce a valid conclusion') from error
    return issue_report(reports, pair, result)
