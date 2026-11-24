from base64 import b64encode
from dataclasses import asdict

from reports.history import read_report
from reports.store import ReportStore
from storage import canonical_json


def export_report(store: ReportStore, report_id: int) -> bytes:
    report = read_report(store, report_id)
    return canonical_json({
        'report_id': report.report_id,
        'result': report.result.to_record(),
        'originals': [b64encode(raw).decode('ascii') for raw in report.originals],
        'receipt': asdict(report.receipt),
    })
