"""Rules-based security screen.

Deterministic, explainable, no LLM involved. Produces a risk score (0-100),
a risk level, a list of findings with rule ids, and a sanitized_body that
strips obvious prompt-injection / role markers before the text is ever
shown to an LLM classifier.

This is the FIRST line of defense: even if the classifier is ever fooled,
these rules run independently and their result (injection_suspected, risk
level) is what the policy engine treats as authoritative for QUARANTINE.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .models import Mail, Finding, SecurityScreen

POLICY_PATH = Path(__file__).resolve().parent.parent / "policy.toml"


def _load_policy() -> dict:
    with open(POLICY_PATH, "rb") as f:
        return tomllib.load(f)


# --- rule pattern definitions -------------------------------------------------

INJ01_PATTERNS = [
    r"ignore all previous instructions",
    r"ignore (the )?previous instructions",
    r"disregard (all|the) (previous|prior) instructions",
    r"you are now in .*(admin|developer|system) mode",
    r"you are now an? ",
    r"system prompt",
    r"as an ai( language model)?,? you",
    r"ignoriere alle vorherigen anweisungen",
    r"ignoriere die vorherigen anweisungen",
    r"missachte (alle )?(vorherigen|bisherigen) anweisungen",
    r"du bist jetzt im (admin|entwickler)[- ]?modus",
    r"admin mode",
    r"neue anweisung",
]

INJ02_PATTERNS = [
    r"\[system\]",
    r"system note",
    r"\bassistant:",
    r"<system>",
    r"to the ai assistant",
    r"an den ki[- ]?assistenten",
    r"note to ai",
]

INJ03_PATTERNS = [
    r"forward (this|the) (thread|email|mail)? ?to",
    r"send all",
    r"customer list",
    r"kundenliste",
    r"price list",
    r"preisliste",
    r"passwords?",
    r"zugangsdaten",
    r"credentials",
    r"complete customer list",
    r"internal (price|attachments|documents)",
]

PHI01_PATTERNS = [
    r"password (is )?about to expire",
    r"password expires",
    r"passwort läuft.*ab",
    r"verify your account",
    r"konto (ist |wird )?gesperrt",
    r"konto gesperrt",
    r"innerhalb von 24 stunden",
    r"within 24 hours",
    r"verifizieren sie (ihr|umgehend)",
]

LOOKALIKE_TLDS = [".xyz", ".top", ".ru", ".click", ".gq", ".tk"]

BEC02_PATTERNS = [
    r"überweisen sie",
    r"überweisung",
    r"bank(verbindung|daten)? (hat sich|haben sich) geändert",
    r"neue iban",
    r"kontodaten (haben sich|hat sich) geändert",
    r"dringend.*überweis",
    r"wire transfer",
    r"change (the )?bank details",
    r"urgent.*payment",
]

URGENCY_PATTERNS = [
    r"heute noch",
    r"sofort",
    r"dringend",
    r"umgehend",
    r"asap",
    r"urgent",
]


def _find_any(patterns: list[str], text: str) -> list[str]:
    hits = []
    low = text.lower()
    for pat in patterns:
        if re.search(pat, low, flags=re.IGNORECASE):
            hits.append(pat)
    return hits


def _looks_like_lookalike_domain(domain: str) -> bool:
    domain = domain.lower()
    if any(domain.endswith(tld) for tld in LOOKALIKE_TLDS):
        return True
    # digits substituted for letters, e.g. m1crosoft
    if re.search(r"[a-z]+\d[a-z]+", domain):
        return True
    if "xn--" in domain:  # punycode
        return True
    return False


def _domain_of(addr_or_url: str) -> str:
    m = re.search(r"@([\w.\-]+)", addr_or_url)
    if m:
        return m.group(1)
    m = re.search(r"https?://([^/\s]+)", addr_or_url)
    if m:
        return m.group(1)
    return addr_or_url


def _check_phishing_links(mail: Mail) -> list[Finding]:
    findings = []
    weights = _load_policy()["security"]["weights"]
    for link in mail.links:
        text_domain = _domain_of(link.text)
        href_domain = _domain_of(link.href)
        mismatch = text_domain and href_domain and text_domain.lower() != href_domain.lower()
        lookalike = _looks_like_lookalike_domain(href_domain)
        if mismatch or lookalike:
            reason = []
            if mismatch:
                reason.append(f"Linktext zeigt '{text_domain}', Ziel ist aber '{href_domain}'")
            if lookalike:
                reason.append(f"verdächtige/lookalike Domain im Linkziel: '{href_domain}'")
            findings.append(
                Finding(
                    rule_id="PHI-02",
                    weight=weights.get("PHI-02", 40),
                    description="Link-Text/Ziel-Mismatch oder Lookalike-Domain: " + "; ".join(reason),
                )
            )
    return findings


def _check_attachments(mail: Mail) -> list[Finding]:
    findings = []
    policy = _load_policy()
    weights = policy["security"]["weights"]
    risky_ext = policy["security"]["attachment_risk_extensions"]["extensions"]
    for att in mail.attachments:
        low = att.lower()
        for ext in risky_ext:
            if low.endswith(ext):
                findings.append(
                    Finding(
                        rule_id="ATT-01",
                        weight=weights.get("ATT-01", 25),
                        description=f"Risikobehafteter Anhangstyp: {att}",
                    )
                )
                break
    return findings


def _check_bec(mail: Mail) -> list[Finding]:
    findings = []
    policy = _load_policy()
    weights = policy["security"]["weights"]
    execs = [e.lower() for e in policy["company"]["executives"]]
    company_domain = policy["company"]["domain"].lower()

    from_name_low = mail.from_name.lower()
    claims_exec = any(ex in from_name_low for ex in execs)
    sender_domain = _domain_of(mail.from_addr).lower()
    external = sender_domain and not sender_domain.endswith(company_domain)

    if claims_exec and external:
        findings.append(
            Finding(
                rule_id="BEC-01",
                weight=weights.get("BEC-01", 40),
                description=(
                    f"Absendername '{mail.from_name}' suggeriert Geschäftsführung, "
                    f"aber Absenderdomain '{sender_domain}' ist nicht die Firmendomain "
                    f"'{company_domain}' (Display-Name-Spoofing)"
                ),
            )
        )

    text = f"{mail.subject}\n{mail.body}"
    payment_hits = _find_any(BEC02_PATTERNS, text)
    urgency_hits = _find_any(URGENCY_PATTERNS, text)
    if payment_hits and urgency_hits:
        findings.append(
            Finding(
                rule_id="BEC-02",
                weight=weights.get("BEC-02", 40),
                description=(
                    "Zahlungs-/Bankdatenänderung kombiniert mit Dringlichkeit: "
                    f"{payment_hits[0]!r} + {urgency_hits[0]!r}"
                ),
            )
        )
    return findings


def _check_injection(mail: Mail) -> list[Finding]:
    findings = []
    policy = _load_policy()
    weights = policy["security"]["weights"]
    text = f"{mail.subject}\n{mail.body}"

    inj01 = _find_any(INJ01_PATTERNS, text)
    if inj01:
        findings.append(
            Finding(
                rule_id="INJ-01",
                weight=weights.get("INJ-01", 45),
                description=f"Anweisungs-Override-Phrase gefunden: {inj01[0]!r}",
            )
        )
    inj02 = _find_any(INJ02_PATTERNS, text)
    if inj02:
        findings.append(
            Finding(
                rule_id="INJ-02",
                weight=weights.get("INJ-02", 35),
                description=f"Rollen-/System-Marker gefunden: {inj02[0]!r}",
            )
        )
    inj03 = _find_any(INJ03_PATTERNS, text)
    if inj03:
        findings.append(
            Finding(
                rule_id="INJ-03",
                weight=weights.get("INJ-03", 45),
                description=f"Exfiltrations-Aufforderung gefunden: {inj03[0]!r}",
            )
        )
    return findings


def _check_phishing_lures(mail: Mail) -> list[Finding]:
    findings = []
    policy = _load_policy()
    weights = policy["security"]["weights"]
    text = f"{mail.subject}\n{mail.body}"
    hits = _find_any(PHI01_PATTERNS, text)
    if hits:
        findings.append(
            Finding(
                rule_id="PHI-01",
                weight=weights.get("PHI-01", 30),
                description=f"Credential-/Dringlichkeits-Köder gefunden: {hits[0]!r}",
            )
        )
    return findings


def sanitize_body(text: str) -> str:
    """Neutralize obvious control/role markers before the text reaches an LLM.

    This does not "clean" the mail into something to trust - it just removes
    the most blatant attempts to look like system/assistant turns, so the
    classifier prompt cannot be structurally confused. The classifier is
    still told (via its system prompt) that the whole mail is untrusted data.
    """
    sanitized = text
    for pat in INJ02_PATTERNS:
        sanitized = re.sub(pat, "[REMOVED-MARKER]", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\[SYSTEM NOTE.*?\]", "[REMOVED-SYSTEM-NOTE]", sanitized, flags=re.IGNORECASE | re.DOTALL)
    sanitized = re.sub(r"<system>.*?</system>", "[REMOVED-SYSTEM-BLOCK]", sanitized, flags=re.IGNORECASE | re.DOTALL)
    return sanitized


def _level_for_score(score: int, thresholds: dict) -> str:
    if score >= thresholds["high"]:
        return "high"
    if score >= thresholds["medium"]:
        return "medium"
    if score >= thresholds["low"]:
        return "low"
    return "none"


def screen(mail: Mail) -> SecurityScreen:
    policy = _load_policy()
    thresholds = policy["security"]["thresholds"]

    findings: list[Finding] = []
    findings += _check_injection(mail)
    findings += _check_phishing_lures(mail)
    findings += _check_phishing_links(mail)
    findings += _check_bec(mail)
    findings += _check_attachments(mail)

    score = min(100, sum(f.weight for f in findings))
    level = _level_for_score(score, thresholds)
    injection_suspected = any(f.rule_id.startswith("INJ") for f in findings)

    sanitized = sanitize_body(mail.body)

    return SecurityScreen(
        risk_score=score,
        risk_level=level,
        findings=findings,
        injection_suspected=injection_suspected,
        sanitized_body=sanitized,
    )
