#!/usr/bin/env python
"""CLI entry point for the Mail Triage Agent.

Usage:
    python run_triage.py [--inbox samples/inbox.json] [--level N]
                          [--provider {auto,anthropic,openai,heuristic}] [--model NAME]
                          [--no-llm] [--out out]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tomllib
from pathlib import Path

from triage.ingest import load_inbox
from triage import security as security_mod
from triage import classify as classify_mod
from triage import policy as policy_mod
from triage import metrics as metrics_mod
from triage import report as report_mod
from triage.models import MailResult

BASE_DIR = Path(__file__).resolve().parent
POLICY_PATH = BASE_DIR / "policy.toml"
ENV_PATH = BASE_DIR / ".env"


def _load_policy() -> dict:
    with open(POLICY_PATH, "rb") as f:
        return tomllib.load(f)


def _load_dotenv(path: Path) -> None:
    """Minimal stdlib .env loader: KEY=VALUE lines, '#' comments, blank lines
    ignored, optional surrounding quotes stripped. Never overrides a variable
    that is already set in the real environment (so `setx`/session exports
    still win over .env)."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Mail Triage Agent for Nordlicht Solar GmbH")
    parser.add_argument("--inbox", default=str(BASE_DIR / "samples" / "inbox.json"))
    parser.add_argument("--level", type=int, default=None, help="Override autonomy level (0-3)")
    parser.add_argument(
        "--provider", default=None, choices=["auto", "anthropic", "openai", "heuristic"],
        help="Classifier backend (default: auto, from TRIAGE_LLM_PROVIDER or env detection)",
    )
    parser.add_argument(
        "--model", default=None,
        help="Override the model name (TRIAGE_LLM_MODEL for --provider openai, TRIAGE_MODEL for anthropic)",
    )
    parser.add_argument("--no-llm", action="store_true", help="Alias for --provider heuristic")
    parser.add_argument("--out", default=str(BASE_DIR / "out"))
    args = parser.parse_args(argv)
    if args.no_llm:
        args.provider = "heuristic"
    return args


def _configure_llm_warning_logging() -> None:
    """Print warnings from triage.classify (rate-limit waits, retries) to
    stdout as `[Hinweis] <message>`. Guarded against duplicate handlers so
    calling main() more than once (e.g. from tests) doesn't double-print."""
    llm_logger = logging.getLogger("triage.classify")
    llm_logger.setLevel(logging.WARNING)
    already_installed = any(
        getattr(h, "_triage_hinweis_handler", False) for h in llm_logger.handlers
    )
    if not already_installed:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[Hinweis] %(message)s"))
        handler._triage_hinweis_handler = True
        llm_logger.addHandler(handler)


def main(argv=None) -> int:
    _configure_llm_warning_logging()
    _load_dotenv(ENV_PATH)
    args = parse_args(argv)
    policy = _load_policy()
    level = args.level if args.level is not None else policy["autonomy"]["level"]

    try:
        provider, settings = classify_mod.resolve_provider(args.provider)
    except ValueError as exc:
        print(f"[Hinweis] {exc} Fallback auf Heuristik.")
        provider, settings = "heuristic", {}

    use_llm = provider != "heuristic"
    model = args.model or settings.get("model")
    if provider == "openai":
        classifier_mode_label = f"openai-compatible:{settings['base_url']}:{model}"
    elif provider == "anthropic":
        classifier_mode_label = f"anthropic:{model}"
    else:
        classifier_mode_label = "heuristic"

    mails = load_inbox(args.inbox)

    security_results = []
    classification_results = []
    results: list[MailResult] = []

    llm_error_shown = False
    for mail in mails:
        sec = security_mod.screen(mail)
        cls = classify_mod.classify(mail, sec, use_llm=use_llm, model=model, provider=provider)
        if cls.classifier.startswith("heuristic (llm error:") and not llm_error_shown:
            print(f"[Hinweis] LLM-Fehler bei Mail {mail.id}, Fallback auf Heuristik: {cls.classifier}")
            print("          (weitere Mails werden still auf Heuristik zurückfallen)")
            llm_error_shown = True
        decision = policy_mod.decide(mail, sec, cls, level=level)
        security_results.append(sec)
        classification_results.append(cls)
        results.append(MailResult(mail=mail, security=sec, classification=cls, decision=decision))

    classifier_modes = {r.classification.classifier for r in results}
    if len(classifier_modes) == 1:
        classifier_mode = next(iter(classifier_modes))
    else:
        classifier_mode = "mixed: " + ", ".join(sorted(classifier_modes))

    kpis = metrics_mod.compute_run_metrics(results, level=level)
    rampup = metrics_mod.compute_rampup(mails, security_results, classification_results)

    out_dir = Path(args.out)
    report_mod.print_console_summary(results, level, classifier_mode, kpis)
    paths = report_mod.write_all(results, kpis, rampup, level, classifier_mode, out_dir)

    print()
    if use_llm:
        n = len(results)
        n_heur = sum(
            1 for r in results if r.classification.classifier.startswith("heuristic")
        )
        n_llm = n - n_heur
        if n_heur == 0:
            print(f"Classifier-Modus: LLM ({classifier_mode_label}), alle {n} Mails per LLM klassifiziert.")
        elif n_llm == 0:
            print(f"Classifier-Modus: {classifier_mode_label} nicht erreichbar, alle Mails per Heuristik klassifiziert.")
        else:
            print(
                f"Classifier-Modus: LLM ({classifier_mode_label}) für {n_llm} von {n} Mails, "
                f"{n_heur} per Heuristik (Fehler siehe Hinweise oben)."
            )
    else:
        print("Classifier-Modus: Heuristik (kein LLM verwendet; --no-llm/--provider heuristic oder kein Provider konfiguriert).")
    print(f"Report geschrieben nach: {paths['report_html'].resolve()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
