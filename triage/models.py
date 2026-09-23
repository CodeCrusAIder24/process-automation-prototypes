"""Dataclasses shared across the triage pipeline.

Keeping these in one place means every stage (security screen, classifier,
policy engine, reply drafting, reporting) speaks the same vocabulary and can
be serialised to JSON in a predictable way.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Link:
    """A link found in a mail body.

    `text` is what the reader sees, `href` is where it actually points.
    For plain-text bodies without markup, text == href.
    """

    text: str
    href: str

    def to_dict(self) -> dict:
        return {"text": self.text, "href": self.href}


@dataclass
class Mail:
    id: str
    from_addr: str
    from_name: str
    to: str
    subject: str
    body: str
    received_at: str
    attachments: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Finding:
    """A single security-rule hit."""

    rule_id: str
    weight: int
    description: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SecurityScreen:
    risk_score: int
    risk_level: str  # none | low | medium | high
    findings: list[Finding] = field(default_factory=list)
    injection_suspected: bool = False
    sanitized_body: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Classification:
    category: str
    confidence: float
    summary: str
    language: str
    sentiment: str
    injection_suspected: bool
    requests_nonpublic_info: bool
    suggested_department: str
    classifier: str = "heuristic"  # or "llm:<model>" or "heuristic (llm error: ...)"
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    action: str  # AUTO_ARCHIVE | AUTO_ROUTE | AUTO_REPLY | DRAFT_FOR_REVIEW | ESCALATE_HUMAN | QUARANTINE
    target: str
    reasons: list[str] = field(default_factory=list)
    requires_human: bool = True
    draft: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MailResult:
    """Full pipeline result for a single mail - what gets written to results.json."""

    mail: Mail
    security: SecurityScreen
    classification: Classification
    decision: Decision

    def to_dict(self) -> dict:
        return {
            "mail": self.mail.to_dict(),
            "security": self.security.to_dict(),
            "classification": self.classification.to_dict(),
            "decision": self.decision.to_dict(),
        }
