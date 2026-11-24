from reports.store import FinalReport, ReportStore


class ReportNotFound(LookupError):
    pass


def read_report(store: ReportStore, report_id: int) -> FinalReport:
    report = store.load_finalized(report_id)
    if report is None:
        raise ReportNotFound(report_id)
    return report
