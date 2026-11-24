import sqlite3

from exchange.pair import StationPair
from reports.store import FinalReport, ReportStore
from review.conclusion import TurnaroundResult, build_conclusion


class IssuanceFailed(RuntimeError):
    pass


def issue_report(store: ReportStore, pair: StationPair, result: TurnaroundResult) -> FinalReport:
    try:
        bound = build_conclusion(pair, result.start_station, result.start_entry, result.deadline, result.by_deadline, result.eventual)
        if bound != result:
            raise ValueError('conclusion does not bind the actual selected originals')
        first, second = pair.pieces
        originals = (first.package.original, second.package.original)
        return store.commit(result, originals)
    except (sqlite3.Error, ValueError) as error:
        raise IssuanceFailed(str(error)) from error
