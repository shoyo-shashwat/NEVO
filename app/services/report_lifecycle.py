# services/report_lifecycle.py
#
# Propagates DemandCluster/Project/Outcome lifecycle events down to the
# individual Report rows that belong to that cluster (Phase 0 §6 — every
# report has a real status, not just its cluster).
#
# Deliberately conservative: only moves a report FORWARD in the lifecycle
# (Submitted → Processing → Verified → Clustered → UnderReview → Actioned →
# Resolved → Closed), never backward, and never touches a report that has
# already reached Resolved/Closed. A cluster-level event (a government
# decision, a project being created, an outcome being verified) is a
# downstream consequence for every report that contributed to it.

from app.extensions import db
from app.models.citizen_models import Report, Contribution

_ORDER = [
    "Draft", "Submitted", "Processing", "Verified", "Clustered",
    "UnderReview", "Actioned", "Resolved", "Closed",
]
_RANK = {name: i for i, name in enumerate(_ORDER)}
_TERMINAL = {"Resolved", "Closed"}


def _reports_for_cluster(cluster_id: str):
    return (
        Report.query
        .join(Contribution, Contribution.report_id == Report.id)
        .filter(Contribution.demand_cluster_id == cluster_id)
        .all()
    )


def _advance(report: Report, target_status: str) -> None:
    if report.status in _TERMINAL:
        return  # never reopen a report that's already Resolved/Closed
    if _RANK.get(report.status, 0) >= _RANK.get(target_status, 0):
        return  # never move backward
    report.status = target_status


def mark_cluster_under_review(cluster_id: str) -> None:
    for report in _reports_for_cluster(cluster_id):
        _advance(report, "UnderReview")


def mark_cluster_actioned(cluster_id: str) -> None:
    for report in _reports_for_cluster(cluster_id):
        _advance(report, "Actioned")


def mark_cluster_resolved(cluster_id: str) -> None:
    for report in _reports_for_cluster(cluster_id):
        _advance(report, "Resolved")
