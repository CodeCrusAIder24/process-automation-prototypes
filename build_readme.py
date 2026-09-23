"""Renders README.md into docs/index.html, styled to match the generated
report (out/report.html, produced by triage/report.py).

Reuses the same design tokens (tokens.css) and the same Google Fonts request
(triage.report.FONTS_HREF) so both pages look like one system. No dependency
beyond the `markdown` package (see requirements.txt) and the standard library.

CLI:
    python build_readme.py
"""
from __future__ import annotations

import re
from pathlib import Path

import markdown

from triage.report import FONTS_HREF

ROOT = Path(__file__).resolve().parent
README_PATH = ROOT / "README.md"
TOKENS_PATH = ROOT / "tokens.css"
DOCS_DIR = ROOT / "docs"
OUT_PATH = DOCS_DIR / "index.html"

H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)
TABLE_OPEN_RE = re.compile(r"<table>")
TABLE_CLOSE_RE = re.compile(r"</table>")
HREF_RE = re.compile(r'href="([^"]*)"')
# A relative href that ends in "/README.md" (or is exactly "README.md")
# targets a portfolio piece's source README - map it to that piece's
# rendered docs/index.html instead, so the reader lands on a formatted
# page rather than raw markdown.
README_LINK_RE = re.compile(r"(?i)(^|/)README\.md$")


def _wrap_tables(html: str) -> str:
    """Wraps every <table> in a scrollable div so wide tables never force
    the page itself to scroll horizontally (only the table does)."""
    html = TABLE_OPEN_RE.sub('<div class="table-scroll"><table>', html)
    html = TABLE_CLOSE_RE.sub("</table></div>", html)
    return html


def _rewrite_relative_links(body_html: str, prefix: str = "../") -> str:
    """docs/index.html lives one directory below the README it was
    rendered from, so every relative link in the README's markdown -
    written as if the file were still at its own location - needs a
    `prefix` (default "../") to still resolve once it's on the docs/ page.

    Anchors (#section), absolute URLs (scheme://...) and mailto: links are
    left untouched. A link that targets another portfolio piece's
    README.md is additionally mapped to that piece's own rendered
    docs/index.html (see README_LINK_RE) - e.g. "invoice_match/README.md"
    becomes "../invoice_match/docs/index.html" - so cross-piece links open
    a formatted page, not a raw markdown file.

    Shared by build_readme.py (this file, repo root) and
    invoice_match/build_readme.py, so both portfolio pieces' docs pages
    resolve their README's relative links the same way.
    """
    def _rewrite(match: re.Match) -> str:
        href = match.group(1)
        if not href or href.startswith("#") or "://" in href or href.startswith("mailto:"):
            return match.group(0)
        new_href = README_LINK_RE.sub(lambda m: (m.group(1) or "") + "docs/index.html", href)
        new_href = prefix + new_href
        return f'href="{new_href}"'

    return HREF_RE.sub(_rewrite, body_html)


def _plain_title(h1_html: str) -> str:
    """Strips any inline markup left inside the h1 (e.g. <code>) for <title>."""
    return re.sub(r"<[^>]+>", "", h1_html).strip()


DOC_CSS = """
  html, body { overflow-x: clip; }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; scrollbar-color: var(--color-rule-strong) var(--color-paper); }
  body {
    margin: 0; background: var(--color-paper); color: var(--color-ink-2);
    font-family: var(--font-body); font-weight: 400; font-size: var(--text-base); line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: var(--color-accent-soft); color: var(--color-ink); }
  :focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; border-radius: 2px; }
  a { color: var(--color-ink); text-decoration: underline; text-decoration-color: var(--color-rule-strong); text-decoration-thickness: 1px; text-underline-offset: 3px;
      transition: text-decoration-color var(--dur-micro) var(--ease-out); }
  a:hover { text-decoration-color: var(--color-accent); }
  .mono { font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: 0.06em; text-transform: uppercase; font-weight: 500; }
  .wrap { max-width: 72rem; margin: 0 auto; padding: 0 var(--space-lg); }

  /* top bar, same shape as the report's nav */
  .nav { position: sticky; top: 0; z-index: var(--z-sticky-nav); height: var(--banner-height); background: var(--color-paper-glass); backdrop-filter: blur(8px); border-bottom: 1px solid var(--color-rule); }
  .nav .wrap { height: 100%; display: flex; align-items: center; justify-content: space-between; gap: var(--space-lg); }
  .wordmark { font-family: var(--font-display); font-weight: 600; font-size: var(--text-base); letter-spacing: -0.02em; color: var(--color-ink); text-decoration: none; white-space: nowrap; line-height: 1; }
  .nav__links { list-style: none; margin: 0; padding: 0; display: flex; gap: var(--space-lg); }
  .nav__links a { color: var(--color-ink-2); text-decoration: none; white-space: nowrap; line-height: 1; padding: var(--space-xs) 0; position: relative; }
  .nav__links a::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2px; background: var(--color-accent); transform: scaleX(0); transform-origin: left; transition: transform var(--dur-short) var(--ease-out); }
  .nav__links a:hover::after, .nav__links a:focus-visible::after { transform: scaleX(1); }

  /* layout: sidebar TOC + prose column */
  .layout { max-width: 72rem; margin: 0 auto; padding: var(--space-xl) var(--space-lg) var(--space-3xl); }
  .sidebar { margin-bottom: var(--space-xl); }
  .toc-summary { cursor: pointer; font-size: var(--text-sm); color: var(--color-ink); font-weight: 500; padding: var(--space-xs) 0; list-style: none; }
  .toc-summary::-webkit-details-marker { display: none; }
  .toc-summary::before { content: "\\25B8"; display: inline-block; margin-right: var(--space-xs); transition: transform var(--dur-micro) var(--ease-out); }
  details[open] > .toc-summary::before { transform: rotate(90deg); }
  .toc { font-size: var(--text-sm); margin-top: var(--space-sm); }
  .toc ul { list-style: none; margin: 0; padding-left: var(--space-md); }
  .toc > ul { padding-left: 0; }
  .toc li { margin: var(--space-3xs) 0; }
  .toc a { display: block; color: var(--color-muted); text-decoration: none; padding: var(--space-3xs) 0 var(--space-3xs) var(--space-sm); border-left: 1px solid var(--color-rule); margin-left: -1px; }
  .toc a:hover { color: var(--color-ink); }
  .toc a.is-active { color: var(--color-ink); border-left: 1px solid var(--color-accent); font-weight: 500; }

  /* prose */
  .prose { max-width: 68ch; overflow-wrap: anywhere; }
  .prose > *:first-child { margin-top: 0; }
  .prose h1, .prose h2, .prose h3, .prose h4 { font-family: var(--font-display); font-weight: 600; letter-spacing: -0.025em; color: var(--color-ink); text-wrap: balance; scroll-margin-top: calc(var(--banner-height) + var(--space-lg)); }
  .prose h1 { font-size: var(--text-2xl); line-height: 1.1; margin: 0 0 var(--space-lg); }
  .prose h2 { font-size: var(--text-xl); line-height: 1.15; margin: var(--space-2xl) 0 var(--space-md); padding-top: var(--space-lg); border-top: 1px solid var(--color-rule); }
  .prose h2:first-of-type { padding-top: 0; border-top: 0; }
  .prose h3 { font-size: var(--text-md); line-height: 1.25; margin: var(--space-xl) 0 var(--space-sm); }
  .prose h4 { font-size: var(--text-base); margin: var(--space-lg) 0 var(--space-2xs); }
  .prose p, .prose ul, .prose ol, .prose blockquote, .prose .table-scroll, .prose pre, .prose hr { margin: 0 0 var(--space-md); }
  .prose ul, .prose ol { padding-left: var(--space-lg); }
  .prose li + li { margin-top: var(--space-2xs); }
  .prose hr { border: 0; border-top: 1px solid var(--color-rule); margin-top: var(--space-2xl); margin-bottom: var(--space-2xl); }
  .prose strong { color: var(--color-ink); font-weight: 600; }
  .prose code { font-family: var(--font-mono); font-size: 0.875em; background: var(--color-paper-2); border: 1px solid var(--color-rule); border-radius: var(--radius-control); padding: 0.1em 0.35em; }
  .prose pre { font-family: var(--font-mono); font-size: var(--text-sm); line-height: 1.5; background: var(--color-paper-2); border: 1px solid var(--color-rule); border-radius: var(--radius-control); padding: var(--space-md); overflow-x: auto; white-space: pre; }
  .prose pre code { background: none; border: 0; padding: 0; font-size: inherit; }
  .prose blockquote { padding-left: var(--space-md); border-left: 1px solid var(--color-rule-strong); color: var(--color-muted); }
  .prose blockquote p { margin: 0; }
  .table-scroll { overflow-x: auto; }
  .prose table { border-collapse: collapse; width: 100%; font-size: var(--text-sm); font-variant-numeric: tabular-nums; }
  .prose th, .prose td { text-align: left; padding: var(--space-sm) var(--space-md) var(--space-sm) 0; border-bottom: 1px solid var(--color-rule); vertical-align: top; }
  .prose thead th { font-family: var(--font-mono); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-muted); font-weight: 500; white-space: nowrap; }

  @media (min-width: 64rem) {
    .layout { display: grid; grid-template-columns: 15rem minmax(0, 1fr); gap: var(--space-2xl); align-items: start; }
    .sidebar { margin-bottom: 0; position: sticky; top: calc(var(--banner-height) + var(--space-lg)); max-height: calc(100vh - var(--banner-height) - var(--space-2xl)); overflow-y: auto; align-self: start; }
    .toc-summary { display: none; }
  }

  @media (max-width: 48rem) {
    .nav__links { display: none; }
  }

  @media print {
    .nav, .sidebar { display: none; }
    .table-scroll { overflow: visible; }
  }

  @media (prefers-reduced-motion: reduce) {
    html { scroll-behavior: auto; }
    * { transition-duration: 1ms !important; }
  }
"""

# Native <details> hides its body through an internal UA shadow wrapper that
# plain CSS on the light-DOM children cannot override, so the only reliable
# way to force the sidebar TOC open at wide viewports (and closed again below
# 64rem) is to toggle the real `open` attribute. Server-rendered HTML omits
# `open`, so a no-JS visitor still gets a working, if always-collapsed,
# disclosure toggle at any width.
TOC_SYNC_JS = """
(function () {
  var d = document.querySelector('details.toc-wrap');
  if (!d) { return; }
  var mq = window.matchMedia('(min-width: 64rem)');
  function sync() { if (mq.matches) { d.setAttribute('open', ''); } else { d.removeAttribute('open'); } }
  sync();
  if (mq.addEventListener) { mq.addEventListener('change', sync); } else { mq.addListener(sync); }
})();
"""

# Highlights the TOC entry for the section currently in view. Plain JS, no
# dependencies; harmless (and inert) under reduced motion since nothing here
# animates in the first place.
DOC_JS = """
(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll('.toc a'));
  if (!links.length || !('IntersectionObserver' in window)) { return; }
  var byId = {};
  links.forEach(function (a) { byId[a.getAttribute('href').slice(1)] = a; });
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      var link = byId[entry.target.id];
      if (!link) { return; }
      link.classList.toggle('is-active', entry.isIntersecting);
    });
  }, { rootMargin: '0px 0px -70% 0px' });
  Object.keys(byId).forEach(function (id) {
    var heading = document.getElementById(id);
    if (heading) { observer.observe(heading); }
  });
})();
"""


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
    title = _plain_title(h1_match.group(1)) if h1_match else "Mail Triage Agent"

    page = f"""<!DOCTYPE html>
<html lang="de">
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
    <a class="wordmark" href="#top">Mail Triage Agent</a>
    <nav aria-label="Links">
      <ul class="nav__links mono">
        <li><a href="../out/report.html">Report &ouml;ffnen</a></li>
        <li><a href="../README.md">README auf GitHub</a></li>
      </ul>
    </nav>
  </div>
</header>

<div class="layout" id="top">
  <aside class="sidebar">
    <details class="toc-wrap">
      <summary class="toc-summary">Inhalt</summary>
      <nav aria-label="Inhaltsverzeichnis">{toc_html}</nav>
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
