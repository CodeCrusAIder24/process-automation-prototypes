"""Renders invoice_match/README.md into invoice_match/docs/index.html, on
the identical design system as piece #1's docs/index.html (build_readme.py
at the repo root).

Single source of truth: this module imports DOC_CSS, TOC_SYNC_JS, DOC_JS,
_wrap_tables and _rewrite_relative_links from the root build_readme.py, and
FONTS_HREF from triage.report - nothing here is a re-typed copy. The repo
root is added to sys.path first so both imports resolve regardless of
whether this script is run from invoice_match/ or invoked as
`python invoice_match/build_readme.py` from the repo root.

CLI:
    python build_readme.py     (run from inside invoice_match/)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import markdown

SCRIPT_DIR = Path(__file__).resolve().parent          # invoice_match/
REPO_ROOT = SCRIPT_DIR.parent
# Insert at index 0 (ahead of the script's own directory, which Python adds
# automatically) so `import build_readme` resolves to the repo-root module,
# not to this file re-importing itself under the same basename.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from build_readme import DOC_CSS, DOC_JS, TOC_SYNC_JS, _rewrite_relative_links, _wrap_tables  # noqa: E402
from triage.report import FONTS_HREF  # noqa: E402

README_PATH = SCRIPT_DIR / "README.md"
TOKENS_PATH = REPO_ROOT / "tokens.css"
DOCS_DIR = SCRIPT_DIR / "docs"
OUT_PATH = DOCS_DIR / "index.html"

H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)


def _plain_title(h1_html: str) -> str:
    return re.sub(r"<[^>]+>", "", h1_html).strip()


def build() -> Path:
    readme_text = README_PATH.read_text(encoding="utf-8")
    tokens_css = TOKENS_PATH.read_text(encoding="utf-8")

    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-3", "permalink": False}},
    )
    body_html = md.convert(readme_text)
    body_html = _rewrite_relative_links(body_html)
    body_html = _wrap_tables(body_html)
    toc_html = md.toc

    h1_match = H1_RE.search(body_html)
    title = _plain_title(h1_match.group(1)) if h1_match else "Invoice Match"

    # The README is primarily English (with a German Kurzfassung section),
    # unlike piece #1's German-labelled page - lang="en" here is the
    # correct accessibility/semantics call for this page's dominant
    # content language; everything else about the page (nav shape,
    # sidebar TOC, prose column, tokens, fonts) matches piece #1 exactly.
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS_HREF}">
<style>
{tokens_css}
{DOC_CSS}</style>
</head>
<body>
<header class="nav" role="banner">
  <div class="wrap">
    <a class="wordmark" href="#top">Invoice Match</a>
    <nav aria-label="Links">
      <ul class="nav__links mono">
        <li><a href="../out/report.html">Report &ouml;ffnen</a></li>
        <li><a href="../../docs/index.html">Mail Triage Agent</a></li>
      </ul>
    </nav>
  </div>
</header>

<div class="layout" id="top">
  <aside class="sidebar">
    <details class="toc-wrap">
      <summary class="toc-summary">Contents</summary>
      <nav aria-label="Table of contents">{toc_html}</nav>
    </details>
  </aside>
  <script>{TOC_SYNC_JS}</script>
  <main class="prose">
{body_html}
  </main>
</div>

<script>{DOC_JS}</script>
</body>
</html>
"""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(page, encoding="utf-8")
    return OUT_PATH


if __name__ == "__main__":
    out = build()
    print(out)
