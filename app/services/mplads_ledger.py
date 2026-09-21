# services/mplads_ledger.py
#
# Pure ledger arithmetic + the guard the spec asks for: figures are checked where
# they ENTER the system (validate), not in a template.
#
# The official portal (mplads.mospi.gov.in) publishes cumulative-for-the-term
# figures: allocated, recommended (by the MP), sanctioned (by the district),
# spent. It publishes no "released" amount (payments go direct to vendors since
# April 2025) and no as-of date, so "read on" is OUR retrieval date, never the
# source's. An MP recommends; the district authority sanctions -- copy says so.

from datetime import date


def days_to(d: date | None, today: date) -> int | None:
    return (d - today).days if d else None


def validate(allocated, recommended, sanctioned, spent):
    """Raise ValueError if the figures cannot all be true at once. None = not reported, skipped."""
    if allocated is not None and recommended is not None and recommended > allocated:
        raise ValueError("recommended exceeds allocated")
    if recommended is not None and sanctioned is not None and sanctioned > recommended:
        raise ValueError("sanctioned exceeds recommended")
    if sanctioned is not None and spent is not None and spent > sanctioned:
        raise ValueError("spent exceeds sanctioned")


def summarize(row, today: date) -> dict | None:
    if row is None:
        return None
    f = lambda v: float(v) if v is not None else None
    rec, san = f(row.recommended), f(row.sanctioned)
    return {
        "seat_label": row.seat_label, "period_label": row.period_label,
        "allocated": f(row.allocated), "recommended": rec, "sanctioned": san, "spent": f(row.spent),
        # recommended by the MP, not yet sanctioned by the district: the pending-action figure
        "awaiting_sanction": (rec - san) if rec is not None and san is not None else None,
        "works_recommended": row.works_recommended, "works_sanctioned": row.works_sanctioned,
        "works_completed": row.works_completed,
        "days_to_term_end": days_to(row.term_end, today),
        "source": row.source, "source_url": row.source_url, "method": row.method,
        "read_on": row.source_last_updated, "notes": row.notes,
    }


def rupees(v) -> str:
    """Lakh/crore display: 12345678 -> ₹1.23 Cr."""
    if v is None:
        return "not reported"
    if v >= 1e7:
        return f"₹{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"₹{v / 1e5:.1f} L"
    return f"₹{v:,.0f}"


if __name__ == "__main__":
    from types import SimpleNamespace as N
    validate(154306950, 69505786, 46729067, 22424674)          # real New Delhi figures
    for bad in [(1e7, 2e7, 1e7, 0), (5e7, 2e7, 3e7, 0), (5e7, 3e7, 2e7, 2.5e7)]:
        try: validate(*bad); raise SystemExit("should have failed")
        except ValueError: pass
    r = N(seat_label="x", period_label="p", allocated=154306950, recommended=69505786, sanctioned=46729067,
          spent=22424674, works_recommended=113, works_sanctioned=79, works_completed=43, term_end=date(2029, 6, 3),
          source=None, source_url=None, method="manual", source_last_updated=None, notes=None)
    s = summarize(r, date(2026, 9, 21))
    assert round(s["awaiting_sanction"]) == 22776719 and rupees(s["awaiting_sanction"]) == "₹2.28 Cr"
    assert s["days_to_term_end"] == 986
    print("ok")
