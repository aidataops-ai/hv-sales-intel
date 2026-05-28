"""Sanity tests for src/md_input.py — the LLM input normalizer."""

from src.md_input import savings_summary, to_markdown


def test_plain_text_passes_through():
    out = to_markdown("Hello world.\nWe staff dentists nationwide.")
    assert "Hello world." in out
    assert "dentists nationwide" in out


def test_html_strips_noise_and_converts_to_markdown():
    html = """<!DOCTYPE html><html><head>
        <style>body { color: red; }</style>
        <script>var x = 1;</script>
    </head><body>
        <nav><a href="/x">Nav junk</a></nav>
        <header>Header junk</header>
        <main>
            <h1>Sunrise Dental Group</h1>
            <p>We are a multi-doctor practice in Tampa.</p>
            <ul>
                <li>Cosmetic dentistry</li>
                <li>Implants</li>
            </ul>
            <h2>Meet our team</h2>
            <p>Dr. Sarah Lin, DDS leads our clinical staff.</p>
        </main>
        <footer>Footer junk</footer>
    </body></html>"""
    out = to_markdown(html)
    assert "# Sunrise Dental Group" in out
    assert "## Meet our team" in out
    assert "- Cosmetic dentistry" in out
    assert "- Implants" in out
    assert "Dr. Sarah Lin, DDS" in out
    # Noise gone
    assert "color: red" not in out
    assert "var x" not in out
    assert "Nav junk" not in out
    assert "Header junk" not in out
    assert "Footer junk" not in out


def test_html_smaller_than_raw():
    # Simulate verbose HTML with lots of class noise.
    html = """<html><body>""" + ("""
        <div class="container mx-auto px-4 py-8 flex flex-row items-center justify-between">
            <div class="text-2xl font-bold leading-tight text-gray-900 dark:text-white">
                <span class="block">We provide</span>
                <span class="block">healthcare staffing</span>
            </div>
        </div>""" * 30) + "</body></html>"
    md = to_markdown(html)
    assert len(md) < len(html) * 0.55  # at least 45% shrink
    assert "healthcare staffing" in md
    assert "container mx-auto" not in md


def test_empty_input_returns_empty():
    assert to_markdown("") == ""
    assert to_markdown(None) == ""
    assert to_markdown(b"") == ""


def test_savings_summary_format():
    s = savings_summary("a" * 1000, "a" * 400)
    assert "before=1000" in s
    assert "after=400" in s
    assert "delta=-60%" in s


def test_pdf_magic_byte_not_decoded_as_text():
    # If we accidentally treat PDF bytes as text we get %PDF-1.4… nonsense
    # in the output. The detector should route it to pypdf and (since the
    # bytes aren't a real PDF) return "" instead of garbage.
    out = to_markdown(b"%PDF-1.4 not actually a valid pdf")
    assert "%PDF-1.4" not in out
