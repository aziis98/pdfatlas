from pdfatlas.core.html_to_markdown import html_to_markdown


def test_plain_text():
    assert html_to_markdown("Hello world") == "Hello world"
    assert html_to_markdown("") == ""
    assert html_to_markdown("   ") == ""


def test_html_entities():
    assert html_to_markdown("A &amp; B &lt; C &gt; D &quot;E&quot; &#39;F&#39;") == "A & B < C > D \"E\" 'F'"


def test_paragraphs_and_breaks():
    html = "<p>First paragraph.</p><p>Second paragraph.</p>"
    assert html_to_markdown(html) == "First paragraph.\n\nSecond paragraph."

    html_br = "First line.<br>Second line.<br/>Third line."
    assert html_to_markdown(html_br) == "First line.\nSecond line.\nThird line."


def test_inline_formatting():
    html = "This is <b>bold</b> and <i>italic</i> and <code>code</code>."
    assert html_to_markdown(html) == "This is **bold** and *italic* and `code`."

    html_strong = "<strong>Strong</strong> and <em>emphasis</em>."
    assert html_to_markdown(html_strong) == "**Strong** and *emphasis*."


def test_links():
    html = 'Check <a href="https://example.com">this link</a> out.'
    assert html_to_markdown(html) == "Check [this link](https://example.com) out."


def test_headings_and_lists():
    html = "<h1>Heading 1</h1><p>Text</p><ul><li>Item 1</li><li>Item 2</li></ul>"
    md = html_to_markdown(html)
    assert "# Heading 1" in md
    assert "- Item 1" in md
    assert "- Item 2" in md


def test_acrobat_rc_xhtml():
    sample = (
        '<?xml version="1.0"?>\n'
        '<body xmlns="http://www.w3.org/1999/xhtml" xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/" '
        'xfa:APIVersion="Acrobat:11.0.0" xfa:spec="2.0.2"  style="font-size:12.0pt;text-align:left;'
        'color:#000000;font-weight:normal;font-style:normal;font-family:sans-serif"><p dir="ltr">'
        '<span style="font-family:sans-serif">a bit overselling, not so much right now</span></p></body>'
    )
    assert html_to_markdown(sample) == "a bit overselling, not so much right now"
