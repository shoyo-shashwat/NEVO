# services/priority_scoring.py
#
# Two-stage priority scoring — pure functions, no DB access, no side effects.
# Computed fresh per request; never stored (Progress Log §15 / Government §11).
#
# Stage 1 — Evidence Confidence
#   Input: InfrastructureGapAssessment + InvestmentAlignment
#   Output: "HIGH" | "MEDIUM" | "LOW" | "NEEDS_VALIDATION"
#   Measures how well-evidenced the underlying problem is.
#
# Stage 2 — Priority
#   Input: Stage 1 confidence + severity + population + gap + trend + alignment
#   Output: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
#   Assesses what to do about it, given credible evidence.
#
# HARD INVARIANTS (Progress Log §15.1 / Master Prompt §6):
#   1. Volume (total_reports / unique_contributors) feeds Stage 1 ONLY.
#      It must never appear in Stage 2 scoring.
#   2. Severity × report_count multiplication is EXPLICITLY FORBIDDEN.
#      A small number of critical reports must be able to outweigh many
#      low-severity reports (see §12 uneven-demand handling example).
#   3. These are pure functions — no writes, no session, no Flask context.

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.services.gap_assessment import InfrastructureGapAssessment
from app.services.investment_alignment import InvestmentAlignment

logger = logging.getLogger(__name__)

EvidenceConfidence = Literal["HIGH", "MEDIUM", "LOW", "NEEDS_VALIDATION"]
Priority = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class Stage1Result:
    confidence: EvidenceConfidence
    reasons: list[str]    # short explanation factors, shown in Evidence Card


@dataclass
class Stage2Result:
    priority: Priority
    reasons: list[str]    # short explanation factors, shown in Evidence Card
    recommended_next_step: str


# ---------------------------------------------------------------------------
# Stage 1 — Evidence Confidence
# ---------------------------------------------------------------------------

def stage1_confidence(
    gap: InfrastructureGapAssessment,
    alignment: InvestmentAlignment,
) -> Stage1Result:
    """
    Derive Evidence Confidence from gap assessment and demand signals.

    Volume (total_reports, unique_contributors) is used here — Stage 1 only.
    Never pass these values to stage2_priority().

    Parameters
    ----------
    gap       : InfrastructureGapAssessment — from gap_assessment.calculate()
    alignment : InvestmentAlignment         — from investment_alignment.calculate()

    Returns
    -------
    Stage1Result with confidence level and explanation factors.
    """
    reasons: list[str] = []

    # Start from the gap assessment's own confidence rating
    base = gap.confidence   # HIGH | MEDIUM | LOW | NEEDS_VALIDATION

    # Downgrade if infrastructure data is stale
    if gap.infra_data_freshness == "stale":
        if base == "HIGH":
            base = "MEDIUM"
        reasons.append("Infrastructure data is stale — verify with updated source")

    # Volume signals (Stage 1 only — do NOT carry these to Stage 2)
    if gap.unique_contributors >= 20:
        reasons.append("High unique contributor count strengthens demand signal")
        if base == "LOW":
            base = "MEDIUM"
    elif gap.unique_contributors >= 5:
        reasons.append("Moderate contributor count supports demand signal")
    else:
        reasons.append("Low contributor count — demand signal is thin")
        if base == "HIGH":
            base = "MEDIUM"

    if gap.total_reports >= 50:
        reasons.append("High report volume corroborates demand")
    elif gap.total_reports == 0:
        reasons.append("No reports — citizen demand absent")
        base = "NEEDS_VALIDATION"

    # Location consistency is implicit (cluster already geo-grouped) — noted
    reasons.append(
        f"Official coverage: {gap.official_coverage or 'unknown'} | "
        f"Data freshness: {gap.infra_data_freshness or 'unknown'}"
    )

    # Alignment adds confidence when gap is confirmed + unaddressed
    if alignment.state == "UNADDRESSED" and base in ("HIGH", "MEDIUM"):
        reasons.append("No relevant government intervention found — gap is unaddressed")
    elif alignment.state == "ALIGNED":
        reasons.append("Active intervention exists — gap may be partially covered")
        if base == "HIGH":
            base = "MEDIUM"

    return Stage1Result(confidence=base, reasons=reasons)


# ---------------------------------------------------------------------------
# Stage 2 — Priority
# ---------------------------------------------------------------------------

def stage2_priority(
    confidence: EvidenceConfidence,
    severity: str | None,
    population_affected: int | None,
    gap_confidence: str,
    trend: str,
    alignment_state: str,
) -> Stage2Result:
    """
    Derive Priority from credible evidence signals.

    IMPORTANT: Do NOT pass total_reports or unique_contributors here.
    Volume is a Stage 1 concern only (Progress Log §15.1).

    Parameters
    ----------
    confidence         : EvidenceConfidence — output of stage1_confidence()
    severity           : str | None — "low" | "medium" | "high" | "critical"
                         (from the DemandCluster's aggregated Report severity)
    population_affected: int | None — from DemographicDataPoint
    gap_confidence     : str — the gap assessment confidence ("HIGH" etc.)
    trend              : str — "stable" | "increasing" | "decreasing"
    alignment_state    : str — one of the five InvestmentAlignment states

    Returns
    -------
    Stage2Result with priority level, explanation factors, and recommended
    next step for the government actor.
    """
    reasons: list[str] = []

    # If evidence itself is weak, priority cannot be HIGH or CRITICAL
    if confidence in ("LOW", "NEEDS_VALIDATION"):
        step = (
            "Request validation from Planning Officer before prioritising"
            if confidence == "NEEDS_VALIDATION"
            else "Gather additional infrastructure evidence before deciding"
        )
        reasons.append(f"Evidence confidence is {confidence} — priority capped at MEDIUM")
        return Stage2Result(priority="LOW", reasons=reasons, recommended_next_step=step)

    # Score severity (never multiplied by volume)
    sev_score = {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(
        (severity or "").lower(), 1
    )
    if severity:
        reasons.append(f"Severity: {severity}")

    # Population
    pop_score = 0
    if population_affected is not None:
        if population_affected >= 50000:
            pop_score = 3
            reasons.append("Very large affected population")
        elif population_affected >= 10000:
            pop_score = 2
            reasons.append("Large affected population")
        elif population_affected >= 1000:
            pop_score = 1
            reasons.append("Moderate affected population")

    # Infrastructure gap
    gap_score = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NEEDS_VALIDATION": 0}.get(
        gap_confidence, 0
    )
    reasons.append(f"Infrastructure gap confidence: {gap_confidence}")

    # Trend modifier
    trend_bonus = 1 if trend == "increasing" else 0
    if trend == "increasing":
        reasons.append("Demand is increasing")
    elif trend == "decreasing":
        reasons.append("Demand is decreasing — may be self-resolving")

    # Alignment modifier: unaddressed gaps score higher than aligned ones
    align_modifier = {
        "UNADDRESSED": 2,
        "PARTIALLY_ADDRESSED": 1,
        "IMPLEMENTATION_ACCESS_GAP": 2,
        "EMERGING_GAP": 1,
        "ALIGNED": 0,
    }.get(alignment_state, 0)
    reasons.append(f"Investment alignment: {alignment_state}")

    total = sev_score + pop_score + gap_score + trend_bonus + align_modifier

    # Map total score to priority band
    # Severity alone can drive CRITICAL (sev=4 + unaddressed=2 = 6 already CRITICAL)
    # This handles the §12 example: 2 critical reports outweigh 20 low-severity ones
    if total >= 9:
        priority: Priority = "CRITICAL"
        step = "Prioritise immediately and route to responsible authority"
    elif total >= 6:
        priority = "HIGH"
        step = "Review evidence detail and consider prioritising"
    elif total >= 3:
        priority = "MEDIUM"
        step = "Monitor and link to existing project if available"
    else:
        priority = "LOW"
        step = "Defer or link to an existing programme"

    # Override: critical severity + unaddressed gap is always at least HIGH
    if sev_score == 4 and alignment_state in ("UNADDRESSED", "IMPLEMENTATION_ACCESS_GAP"):
        if priority not in ("CRITICAL", "HIGH"):
            priority = "HIGH"
            reasons.append("Critical severity overrides low-score cap")
            step = "Review evidence detail and consider prioritising"

    return Stage2Result(priority=priority, reasons=reasons, recommended_next_step=step)
