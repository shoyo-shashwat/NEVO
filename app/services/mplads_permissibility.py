# services/mplads_permissibility.py
#
# Rule-based "can MPLADS pay for this?" lookup, keyed by demand category code.
# A lookup table, not a model -- no AI touches this (see NEVO_MP_MODULE_SPEC.md
# Stage A). Category-grain only: it says whether the *kind* of work is normally
# eligible, never whether a specific proposed work is.
#
# GUIDELINES_VERSION / REVIEWED_ON must be kept next to the table (risk R5):
# MPLADS guidelines change and a stale table would recommend prohibited works.
# ponytail: category-level only; add per-work checks when the ledger lands.

GUIDELINES_VERSION = "MPLADS Revised Guidelines, MoSPI, effective 1 Apr 2023 (mplads.gov.in)"
REVIEWED_ON = "2026-09-21"

# status: yes | review | no.  Every note says what an MP may *recommend*.
PERMISSIBILITY = {
    "water_sanitation": ("yes", "Drinking water and sanitation assets are permitted."),
    "roads_transport": ("yes", "Roads and public transport infrastructure are permitted."),
    "education_access": ("yes", "School buildings, labs and equipment are permitted."),
    "waste_environment": ("yes", "Drainage and solid-waste assets are permitted."),
    "healthcare_access": ("review", "Buildings and equipment are permitted; staff and medicines are not."),
    "electricity_utilities": ("review", "Street lights and solar are permitted; grid supply is the utility's job."),
}

_UNKNOWN = ("review", "No rule on file for this category.")


def check(category_code: str) -> dict:
    status, note = PERMISSIBILITY.get(category_code, _UNKNOWN)
    return {"status": status, "note": note}


def verdict(alignment_state: str, category_code: str) -> dict:
    """
    One plain answer to "should I spend MPLADS here?". Existing coverage beats
    eligibility: if a scheme already covers the area but is not reaching people
    (IMPLEMENTATION_ACCESS_GAP) the recommendation is to escalate, not to fund.
    kind: escalate | covered | yes | review | no
    """
    if alignment_state == "IMPLEMENTATION_ACCESS_GAP":
        return {"kind": "escalate", "label": "Escalate, don't fund",
                "note": "A scheme already covers this area but isn't reaching people. Press the department to deliver it; MPLADS money is better kept for gaps no scheme owes."}
    if alignment_state == "ALIGNED":
        return {"kind": "covered", "label": "Already covered",
                "note": "Existing investment already addresses this."}
    p = check(category_code)
    label = {"yes": "MPLADS can fund this", "review": "Check MPLADS eligibility", "no": "MPLADS cannot fund this"}[p["status"]]
    return {"kind": p["status"], "label": label, "note": p["note"]}


if __name__ == "__main__":
    assert check("water_sanitation")["status"] == "yes"
    assert check("healthcare_access")["status"] == "review"
    assert check("nonsense")["status"] == "review"
    assert verdict("IMPLEMENTATION_ACCESS_GAP", "water_sanitation")["kind"] == "escalate"
    assert verdict("ALIGNED", "water_sanitation")["kind"] == "covered"
    assert verdict("UNADDRESSED", "water_sanitation")["kind"] == "yes"
    print("ok")
