"""Before/after time-savings ESTIMATES for a triage run.

Everything here is an estimate, built from assumptions in policy.toml
[metrics]. Numbers are illustrative, not measured production data - the
report/README must label them clearly as such.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from .models import MailResult
from . import policy as policy_engine

POLICY_PATH = Path(__file__).resolve().parent.parent / "policy.toml"

AUTO_ACTIONS = {"AUTO_ARCHIVE", "AUTO_ROUTE", "AUTO_REPLY"}


def _load_policy() -> dict:
    with open(POLICY_PATH, "rb") as f:
        return tomllib.load(f)


def compute_run_metrics(results: list[MailResult], level: int | None = None) -> dict:
    policy = _load_policy()
    metrics_cfg = policy["metrics"]
    if level is None:
        level = policy["autonomy"]["level"]
    manual_minutes_map = metrics_cfg["manual_minutes"]  # handling time AFTER triage, per category
    triage_minutes = metrics_cfg["triage_minutes"]
    assisted_triage_minutes = metrics_cfg["assisted_triage_minutes"]
    draft_review_minutes = metrics_cfg["draft_review_minutes"]
    quarantine_confirm_minutes = metrics_cfg["quarantine_confirm_minutes"]
    auto_spot_check_minutes = metrics_cfg["auto_spot_check_minutes"]
    if level == 0:
        # Observe mode: the human is CHECKING the AI, not relying on it, so every
        # mail is still read in full. Only prepared drafts earn a time credit.
        assisted_triage_minutes = triage_minutes
        quarantine_confirm_minutes = triage_minutes
    mails_per_day = metrics_cfg["mails_per_day"]
    working_days = metrics_cfg["working_days_per_month"]

    total = len(results)
    auto_handled = 0
    human_required = 0
    threats_blocked = 0
    injection_detected = 0

    minutes_before = 0.0
    minutes_after = 0.0

    for r in results:
        category = r.classification.category
        handling_minutes = manual_minutes_map.get(category, 3)
        # Baseline: a human triages EVERY mail (read + decide + forward) and then
        # handles it - nobody gets a free pass just because a mail is boring.
        minutes_before += triage_minutes + handling_minutes

        action = r.decision.action
        if action in AUTO_ACTIONS:
            auto_handled += 1
        else:
            human_required += 1

        if action == "QUARANTINE":
            threats_blocked += 1
        if r.security.injection_suspected:
            injection_detected += 1

        if action == "AUTO_ARCHIVE":
            minutes_after += 0.0  # filed automatically, nobody looks at it
        elif action == "AUTO_REPLY":
            minutes_after += 0.1  # sent automatically, spot-checked in aggregate, not per mail
        elif action == "AUTO_ROUTE":
            minutes_after += auto_spot_check_minutes + (draft_review_minutes if r.decision.draft else 0.0)
        elif action == "DRAFT_FOR_REVIEW":
            minutes_after += assisted_triage_minutes + draft_review_minutes
        elif action == "ESCALATE_HUMAN":
            # AI already triaged (category + summary) - human still spends the full
            # handling time, but is spared the from-scratch triage_minutes.
            minutes_after += assisted_triage_minutes + handling_minutes
        elif action == "QUARANTINE":
            minutes_after += quarantine_confirm_minutes

    saved_minutes = max(0.0, minutes_before - minutes_after)
    saved_pct = (saved_minutes / minutes_before * 100.0) if minutes_before > 0 else 0.0

    # Extrapolate the observed per-mail averages to mails_per_day / month.
    avg_before_per_mail = minutes_before / total if total else 0.0
    avg_after_per_mail = minutes_after / total if total else 0.0
    monthly_mails = mails_per_day * working_days
    monthly_minutes_before = avg_before_per_mail * monthly_mails
    monthly_minutes_after = avg_after_per_mail * monthly_mails
    monthly_hours_saved = (monthly_minutes_before - monthly_minutes_after) / 60.0

    return {
        "total_mails": total,
        "auto_handled": auto_handled,
        "auto_handled_pct": round(auto_handled / total * 100, 1) if total else 0.0,
        "human_required": human_required,
        "human_required_pct": round(human_required / total * 100, 1) if total else 0.0,
        "threats_blocked": threats_blocked,
        "injection_attempts_detected": injection_detected,
        "minutes_before": round(minutes_before, 1),
        "minutes_after": round(minutes_after, 1),
        "minutes_saved": round(saved_minutes, 1),
        "saved_pct": round(saved_pct, 1),
        "monthly_hours_saved": round(monthly_hours_saved, 1),
        "mails_per_day": mails_per_day,
        "working_days_per_month": working_days,
    }


def compute_rampup(mail, security_results, classification_results) -> list[dict]:
    """Recompute the whole pipeline's policy decision for levels 0-3 to show
    the ramp-up curve, without re-running the security screen/classifier
    (those don't depend on the autonomy level).
    """
    rampup = []
    for level in (0, 1, 2, 3):
        results = []
        for m, sec, cls in zip(mail, security_results, classification_results):
            decision = policy_engine.decide(m, sec, cls, level=level)
            results.append(MailResult(mail=m, security=sec, classification=cls, decision=decision))
        m_metrics = compute_run_metrics(results, level=level)
        rampup.append(
            {
                "level": level,
                "auto_handled_pct": m_metrics["auto_handled_pct"],
                "monthly_hours_saved": m_metrics["monthly_hours_saved"],
                "minutes_saved": m_metrics["minutes_saved"],
                "saved_pct": m_metrics["saved_pct"],
            }
        )
    return rampup
