"""
Lightweight HTML/XHTML to Markdown converter using Python's standard library.
Converts common rich text markup found in PDF annotations (e.g. Acrobat /RC XML)
into clean Markdown.
"""

from html import unescape
from html.parser import HTMLParser
import re


class _HTMLToMarkdownParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result: list[str] = []
        self._tag_stack: list[str] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self._in_list_item = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        self._tag_stack.append(tag_lower)
        attr_dict = dict(attrs)

        if tag_lower in ("p", "div"):
            if self.result and not self.result[-1].endswith("\n"):
                self.result.append("\n\n")
            elif self.result and not self.result[-1].endswith("\n\n"):
                self.result.append("\n")
        elif tag_lower == "br":
            self.result.append("\n")
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_lower[1])
            if self.result and not self.result[-1].endswith("\n"):
                self.result.append("\n\n")
            self.result.append("#" * level + " ")
        elif tag_lower in ("b", "strong"):
            self.result.append("**")
        elif tag_lower in ("i", "em"):
            self.result.append("*")
        elif tag_lower == "code":
            self.result.append("`")
        elif tag_lower == "a":
            self._link_href = attr_dict.get("href") or ""
            self._link_text = []
        elif tag_lower == "li":
            self._in_list_item = True
            if self.result and not self.result[-1].endswith("\n"):
                self.result.append("\n")
            self.result.append("- ")

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in ("b", "strong"):
            self.result.append("**")
        elif tag_lower in ("i", "em"):
            self.result.append("*")
        elif tag_lower == "code":
            self.result.append("`")
        elif tag_lower == "a":
            text = "".join(self._link_text).strip()
            href = self._link_href or ""
            if href and text:
                self.result.append(f"[{text}]({href})")
            elif text:
                self.result.append(text)
            elif href:
                self.result.append(f"<{href}>")
            self._link_href = None
            self._link_text = []
        elif tag_lower in ("p", "div"):
            if self.result and not self.result[-1].endswith("\n"):
                self.result.append("\n\n")
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if self.result and not self.result[-1].endswith("\n"):
                self.result.append("\n\n")
        elif tag_lower == "li":
            self._in_list_item = False
            if self.result and not self.result[-1].endswith("\n"):
                self.result.append("\n")

        if self._tag_stack and self._tag_stack[-1] == tag_lower:
            self._tag_stack.pop()

    def handle_data(self, data: str):
        if self._link_href is not None:
            self._link_text.append(data)
        else:
            self.result.append(data)


def html_to_markdown(html_content: str) -> str:
    """
    Convert an HTML/XHTML fragment or document string into Markdown.
    Decodes HTML entities and strips excessive consecutive newlines.
    """
    if not html_content or not html_content.strip():
        return ""

    # Clean XML declaration or doctype if present
    content = re.sub(r"<\?xml.*?\?>", "", html_content, flags=re.IGNORECASE)
    content = re.sub(r"<!DOCTYPE.*?>", "", content, flags=re.IGNORECASE)

    # Check if there are any HTML tags
    if not re.search(r"<[a-zA-Z][^>]*>", content):
        return unescape(content).strip()

    parser = _HTMLToMarkdownParser()
    try:
        parser.feed(content)
        parser.close()
        text = "".join(parser.result)
    except Exception:
        # Fallback to regex stripping if HTML parsing encounters issues
        text = re.sub(r"<[^>]+>", "", content)

    text = unescape(text)

    # Normalize whitespace: collapse 3+ consecutive newlines to 2, and trim trailing whitespace per line
    lines = [line.rstrip() for line in text.splitlines()]
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized
