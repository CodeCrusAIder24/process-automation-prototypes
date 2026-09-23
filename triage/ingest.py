"""Load a mock inbox (JSON file) into a list of Mail objects.

Adapter interface (comment only, no implementation in this prototype):
    To plug in a real mailbox, implement a class with the same shape as
    `JsonInboxSource` below, e.g.:

        class GraphInboxSource:
            def fetch(self) -> list[Mail]: ...

        class ImapInboxSource:
            def fetch(self) -> list[Mail]: ...

    Both would authenticate against Microsoft Graph / IMAP, page through the
    mailbox in READ-ONLY mode, and map each message to the same Mail
    dataclass used here, so the rest of the pipeline (security screen,
    classifier, policy engine, reply drafting) does not need to change at
    all when the data source changes. Sending replies would go through a
    separate, explicitly human-approved outbox connector - never through
    the ingest path.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Mail, Link


def _parse_links(raw_links: list) -> list[Link]:
    links: list[Link] = []
    for item in raw_links or []:
        if isinstance(item, str):
            links.append(Link(text=item, href=item))
        elif isinstance(item, dict):
            text = item.get("text", item.get("href", ""))
            href = item.get("href", text)
            links.append(Link(text=text, href=href))
    return links


class JsonInboxSource:
    """Reads mails from a local JSON file - stand-in for a real mailbox."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self) -> list[Mail]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        mails: list[Mail] = []
        for item in data:
            mails.append(
                Mail(
                    id=item["id"],
                    from_addr=item.get("from", ""),
                    from_name=item.get("from_name", ""),
                    to=item.get("to", ""),
                    subject=item.get("subject", ""),
                    body=item.get("body", ""),
                    received_at=item.get("received_at", ""),
                    attachments=list(item.get("attachments", [])),
                    links=_parse_links(item.get("links", [])),
                )
            )
        return mails


def load_inbox(path: str | Path) -> list[Mail]:
    return JsonInboxSource(path).fetch()
