"""
Build the docs site (``docs/*.html``) from the Markdown sources (``docs/*.md``).

The Markdown is the source of truth; every page gets the same sidebar, topbar
and ``docs/style.css``.  Run after editing any doc::

    python scripts/build_docs.py          # rewrite docs/*.html
    python scripts/build_docs.py --check  # exit 1 if a page is out of date

Links between docs (``models.md#activity``) become links between pages
(``models.html#activity``).  Heading ids follow GitHub's rules, so the same
anchors work on GitHub and in the HTML.  Links to a missing page, heading or
repository file are reported and make the script exit 1.
"""
import argparse
import html
import re
import sys
from pathlib import Path

import markdown
from markdown.extensions.toc import TocExtension

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"

# Sidebar sections and pages, in order.  Every docs/*.md must appear here.
NAV = [
    ("Getting Started", [("index", "Overview"), ("setup", "Setup")]),
    ("Reference", [
        ("architecture", "Architecture"),
        ("strava-api", "Strava API"),
        ("models", "Models"),
        ("dashboard", "Dashboard"),
        ("management-commands", "Management Commands"),
    ]),
    ("Algorithms", [("best-effort", "Best-Effort Detection")]),
]
LABELS = {stem: label for _, pages in NAV for stem, label in pages}

_HREF = re.compile(r'href="([^"]*)"')
_DOC_LINK = re.compile(r"^([\w-]+)\.md(#.*)?$")
# An intro paragraph: right under the title, followed by a section heading.
_LEAD = re.compile(r"(</h1>\s*)<p>(?!File:)((?:(?!</p>).)*</p>\s*<h2)", re.DOTALL)


def github_slug(text: str, separator: str) -> str:
    """Turn a heading into an id the way GitHub does.

    Args:
        text: The heading's plain text.
        separator: What replaces each space (``-``).

    Returns:
        The lower-cased heading without punctuation, with spaces replaced.
    """
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", separator)


def _page_link(match: re.Match) -> str:
    href = _DOC_LINK.sub(r"\1.html\2", match[1])
    return f'href="{href}"'


def render(md_text: str) -> str:
    """Convert one doc's Markdown to the HTML for the page's content area.

    Args:
        md_text: The Markdown source.

    Returns:
        HTML with ``.md`` links pointing at ``.html`` pages, tables wrapped for
        sideways scrolling and any intro paragraph styled as the page lead.
    """
    body = markdown.markdown(
        md_text, extensions=["tables", "fenced_code", TocExtension(slugify=github_slug)]
    )
    body = _HREF.sub(_page_link, body)
    body = body.replace("<table>", '<div class="table-wrap">\n<table>')
    body = body.replace("</table>", "</table>\n</div>")
    return _LEAD.sub(r'\1<p class="page-lead">\2', body, count=1)


def page(stem: str, title: str, body: str) -> str:
    """Wrap a page's content in the shared layout.

    Args:
        stem: The page's file name without extension, e.g. ``setup``.
        title: The page's first heading.
        body: The content HTML from :func:`render`.

    Returns:
        The complete HTML document.
    """
    nav = []
    for section, pages in NAV:
        nav.append(f'    <div class="nav-section">{section}</div>')
        for other, label in pages:
            active = ' class="active"' if other == stem else ""
            nav.append(f'    <a href="{other}.html"{active}>{label}</a>')
    nav_html = "\n".join(nav)
    home = stem == "index"
    head_title = "StravaXEng Docs" if home else f"{html.escape(title)} — StravaXEng Docs"
    docs_crumb = "<span>Docs</span>" if home else '<a href="index.html">Docs</a>'
    return f"""<!DOCTYPE html>
<!-- Generated from {stem}.md by scripts/build_docs.py. Edit the Markdown, not this file. -->
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{head_title}</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>

<aside class="sidebar">
  <a class="sidebar-brand" href="index.html">
    <div class="logo">
      <svg width="18" height="18" viewBox="0 0 64 64" fill="none">
        <path d="M26 52L38 12l10 24 6-12" stroke="#fff" stroke-width="6"
              stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </div>
    <span>StravaXEng Docs</span>
  </a>
  <nav>
{nav_html}
  </nav>
  <div class="sidebar-footer">StravaXEng &mdash; 2026</div>
</aside>

<div class="main">
  <div class="topbar">
    <div class="breadcrumb">
      {docs_crumb}
      <span class="sep">/</span>
      <span>{LABELS[stem]}</span>
    </div>
  </div>

  <div class="content">
{body}
  </div>
</div>

</body>
</html>
"""


def broken_links(bodies: dict[str, str]) -> list[str]:
    """Find links to pages, headings or repository files that don't exist.

    Args:
        bodies: Rendered content HTML by page stem.

    Returns:
        One ``"<page>.md: <href>"`` line per broken link.
    """
    ids = {stem: set(re.findall(r'id="([^"]+)"', body)) for stem, body in bodies.items()}
    broken = []
    for stem, body in bodies.items():
        for href in _HREF.findall(body):
            if re.match(r"^[a-z]+:", href):  # http:, https:, mailto:
                continue
            target, _, anchor = href.partition("#")
            if not target or ("/" not in target and target.endswith(".html")):  # a docs page
                target_stem = target.removesuffix(".html") or stem
                ok = target_stem in bodies and (not anchor or anchor in ids[target_stem])
            else:
                ok = (DOCS_DIR / target).exists()
            if not ok:
                broken.append(f"{stem}.md: {href}")
    return broken


def main() -> int:
    """Build or check the docs.

    Returns:
        Process exit code: 0 if every page is current and every link resolves.
    """
    parser = argparse.ArgumentParser(description="Build docs/*.html from docs/*.md.")
    parser.add_argument(
        "--check", action="store_true", help="report out-of-date pages instead of writing them"
    )
    check = parser.parse_args().check

    sources = {p.stem: p.read_text(encoding="utf-8") for p in sorted(DOCS_DIR.glob("*.md"))}
    if set(sources) != set(LABELS):
        print(f"docs/*.md and NAV differ: {sorted(set(sources) ^ set(LABELS))}", file=sys.stderr)
        return 1

    bodies = {stem: render(text) for stem, text in sources.items()}
    problems = [f"broken link in {line}" for line in broken_links(bodies)]
    for stem, body in bodies.items():
        title = re.search(r"^# (.+)$", sources[stem], re.MULTILINE)[1]
        new = page(stem, title, body)
        out = DOCS_DIR / f"{stem}.html"
        old = out.read_text(encoding="utf-8").replace("\r\n", "\n") if out.exists() else None
        if new == old:
            continue
        if check:
            problems.append(f"{out.name} is out of date: run python scripts/build_docs.py")
        else:
            out.write_text(new, encoding="utf-8", newline="\n")
            print(f"wrote docs/{out.name}")

    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
