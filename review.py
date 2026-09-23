#!/usr/bin/env python
"""Human feedback loop CLI for the Mail Triage Agent.

    python review.py list                       - show decisions requiring human review
    python review.py approve <id> [--note TEXT]  - record agreement with the AI decision
    python review.py reject <id> [--note TEXT]   - record disagreement with the AI decision
    python review.py stats                       - agreement rate + ramp-up recommendation

Feedback is appended to out/feedback.jsonl. This is deliberately tiny: a real
deployment would probably tie this into the mailbox UI, but the trust-ramp
logic (policy.toml [trust]) is the same either way.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
POLICY_PATH = BASE_DIR / "policy.toml"
DEFAULT_OUT = BASE_DIR / "out"


def _load_policy() -> dict:
    with open(POLICY_PATH, "rb") as f:
        return tomllib.load(f)


def _load_results(out_dir: Path) -> dict:
    results_path = out_dir / "results.json"
    if not results_path.exists():
        print(f"Keine Ergebnisse gefunden unter {results_path}. Zuerst 'python run_triage.py' ausführen.")
        sys.exit(1)
    return json.loads(results_path.read_text(encoding="utf-8"))


def _load_feedback(out_dir: Path) -> list[dict]:
    path = out_dir / "feedback.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def cmd_list(out_dir: Path) -> None:
    data = _load_results(out_dir)
    needs_review = [m for m in data["mails"] if m["decision"]["requires_human"]]
    if not needs_review:
        print("Keine Mails erfordern aktuell menschliche Prüfung.")
        return
    print(f"{len(needs_review)} Mail(s) erfordern menschliche Prüfung:")
    print("-" * 90)
    for m in needs_review:
        print(
            f"{m['mail']['id']:<6} {m['decision']['action']:<16} "
            f"{m['classification']['category']:<22} -> {m['decision']['target']:<20} "
            f"| {m['mail']['subject'][:40]}"
        )


def _append_feedback(out_dir: Path, mail_id: str, verdict: str, note: str, data: dict) -> None:
    match = next((m for m in data["mails"] if m["mail"]["id"] == mail_id), None)
    if match is None:
        print(f"Mail-ID '{mail_id}' nicht in out/results.json gefunden.")
        sys.exit(1)
    out_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mail_id": mail_id,
        "verdict": verdict,  # "approve" or "reject"
        "ai_action": match["decision"]["action"],
        "ai_category": match["classification"]["category"],
        "note": note,
    }
    path = out_dir / "feedback.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"Feedback gespeichert: {mail_id} -> {verdict}")


def cmd_approve(out_dir: Path, mail_id: str, note: str) -> None:
    data = _load_results(out_dir)
    _append_feedback(out_dir, mail_id, "approve", note, data)


def cmd_reject(out_dir: Path, mail_id: str, note: str) -> None:
    data = _load_results(out_dir)
    _append_feedback(out_dir, mail_id, "reject", note, data)


def cmd_stats(out_dir: Path) -> None:
    policy = _load_policy()
    trust = policy["trust"]
    feedback = _load_feedback(out_dir)
    reviewed = len(feedback)
    approved = sum(1 for f in feedback if f["verdict"] == "approve")
    agreement = (approved / reviewed) if reviewed else 0.0

    print(f"Reviewte Entscheidungen: {reviewed}")
    print(f"Davon bestätigt:        {approved}")
    print(f"Zustimmungsrate:         {agreement:.2%}")
    print()
    print(f"Schwellenwerte für Ramp-up: min_reviewed={trust['min_reviewed']}, "
          f"min_agreement={trust['min_agreement']:.0%}")

    if reviewed >= trust["min_reviewed"] and agreement >= trust["min_agreement"]:
        print("-> Empfehlung: Ramp-up auf die nächste Autonomiestufe ist gerechtfertigt.")
    else:
        missing = []
        if reviewed < trust["min_reviewed"]:
            missing.append(f"{trust['min_reviewed'] - reviewed} weitere Reviews nötig")
        if agreement < trust["min_agreement"]:
            missing.append("Zustimmungsrate noch zu niedrig")
        print("-> Empfehlung: Noch nicht ramp-up-bereit (" + "; ".join(missing) + ").")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Human review loop for Mail Triage Agent")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list")

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("mail_id")
    p_approve.add_argument("--note", default="")

    p_reject = sub.add_parser("reject")
    p_reject.add_argument("mail_id")
    p_reject.add_argument("--note", default="")

    sub.add_parser("stats")

    args = parser.parse_args(argv)
    out_dir = Path(args.out)

    if args.command == "list":
        cmd_list(out_dir)
    elif args.command == "approve":
        cmd_approve(out_dir, args.mail_id, args.note)
    elif args.command == "reject":
        cmd_reject(out_dir, args.mail_id, args.note)
    elif args.command == "stats":
        cmd_stats(out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
