"""Normalize any raw input (HTML, PDF bytes, plain text) to clean Markdown
before sending to the LLM.

Why: feeding raw HTML to GPT wastes ~30-50% of the prompt tokens on
markup noise (`<div class="...">`, inline styles, repeated whitespace).
Markdown is the densest faithful representation — same content, far fewer
tokens — and it preserves headings, lists, and links so the model still
gets structure.

Public API:
    to_markdown(content, *, source_hint=None) -> str
    pdf_bytes_to_markdown(data: bytes) -> str   # convenience

`content` may be a str (HTML or plain text) or bytes (PDF or any
text-like blob). The helper auto-detects HTML vs. plain text via a
loose tag-presence check; pass `source_hint="html" | "text" | "pdf"`
to force a path.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Literal

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

log = logging.getLogger(__name__)

SourceHint = Literal["html", "text", "pdf"]

# Tags that almost always contain noise (nav, ads, telemetry, hidden
# state). Stripped from HTML before MD conversion.
_NOISE_TAGS = (
    "script", "style", "nav", "footer", "header", "aside",
    "iframe", "noscript", "form", "svg", "button", "input",
)

# Heuristic: if the string contains any of these tags it's HTML.
# Restricted to structural tags so a casual `<br>` in plain text
# doesn't falsely flip the path.
_HTML_SNIFF = re.compile(
    r"<(html|body|div|p|h[1-6]|article|main|section|table|li|ul|ol)\b",
    re.IGNORECASE,
)

_PDF_MAGIC = b"%PDF"


def to_markdown(
    content: str | bytes | None,
    *,
    source_hint: SourceHint | None = None,
) -> str:
    """Convert content to clean Markdown.

    Always returns a string (possibly empty). Never raises — failure
    modes fall back to plain-text whitespace collapse so the LLM still
    gets _something_.
    """
    if not content:
        return ""

    # --- PDF -----------------------------------------------------------
    if source_hint == "pdf" or (isinstance(content, bytes) and content[:4] == _PDF_MAGIC):
        if isinstance(content, bytes):
            return pdf_bytes_to_markdown(content)
        log.warning("[md_input.pdf_hint_with_str] source_hint=pdf but content is str — treating as text")

    # Decode bytes-that-aren't-PDF as utf-8.
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8", errors="replace")
        except Exception:
            return ""

    if not isinstance(content, str):
        return ""

    text = content.strip()
    if not text:
        return ""

    # --- HTML ----------------------------------------------------------
    if source_hint == "html" or (source_hint is None and _HTML_SNIFF.search(text)):
        return _html_to_markdown(text)

    # --- Plain text ----------------------------------------------------
    return _collapse_whitespace(text)


def pdf_bytes_to_markdown(data: bytes) -> str:
    """Extract text from a PDF and wrap each page in a heading.

    Uses pypdf — pure-Python, no system deps. Returns "" on any error.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("[md_input.pdf_no_pypdf] pypdf not installed; cannot parse PDF")
        return ""

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as e:
        log.warning("[md_input.pdf_open_fail] %s", e)
        return ""

    pages: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        t = t.strip()
        if t:
            pages.append(f"## Page {i}\n\n{t}")
    return _collapse_whitespace("\n\n".join(pages))


def _html_to_markdown(html: str) -> str:
    """Strip noisy tags, then convert to Markdown."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return _collapse_whitespace(html)

    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    # Drop hidden / display:none elements that AI shouldn't see.
    for tag in soup.find_all(attrs={"hidden": True}):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.IGNORECASE)):
        tag.decompose()

    try:
        md = _markdownify(
            str(soup),
            heading_style="ATX",          # `# H1` not `H1\n===`
            bullets="-",                  # consistent list marker
            strip=[],                     # keep anchors (tel:/mailto: matter)
            escape_underscores=False,     # don't escape underscores in URLs
        )
    except Exception as e:
        log.warning("[md_input.md_fail] %s — falling back to get_text", e)
        md = soup.get_text(separator="\n", strip=True)

    return _collapse_whitespace(md)


def _collapse_whitespace(md: str) -> str:
    # Trim trailing space per-line, collapse runs of >2 blank lines, then
    # trim outer whitespace. Keeps paragraph breaks but drops extra noise.
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Collapse 4+ spaces inside a line (markdownify can emit indent runs).
    md = re.sub(r"[ \t]{4,}", "  ", md)
    return md.strip()


def savings_summary(raw: str | bytes, md: str) -> str:
    """Build a `before=N after=M -P%` log fragment for observability."""
    raw_len = len(raw) if raw else 0
    md_len = len(md)
    if raw_len <= 0:
        return f"before=0 after={md_len}"
    pct = round((1 - md_len / raw_len) * 100)
    return f"before={raw_len} after={md_len} delta=-{pct}%"
