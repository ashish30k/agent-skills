#!/usr/bin/env python3
"""Self-tests for check_doc.py. Standard library only.

Run: python3 -I scripts/test_check_doc.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import check_doc  # noqa: E402

GOLDEN = os.path.join(ROOT, "evals", "golden", "sample-session-architecture.md")
FIXTURE = os.path.join(ROOT, "evals", "fixtures", "sample-app")

GOOD_HTML = (
    '<!DOCTYPE html><html lang="en"><head><title>t</title><style>'
    "@media (prefers-color-scheme: dark){body{color:#fff}} @media print{nav{display:none}}"
    '</style></head><body><h1>t</h1><a href="https://example.com/ticket/1">ticket</a>'
    '<svg role="img"></svg></body></html>'
)


def golden_text():
    with open(GOLDEN, encoding="utf-8") as fh:
        return fh.read()


def run_text(text, html=None, repo=FIXTURE):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "doc.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        hp = None
        if html is not None:
            hp = os.path.join(d, "doc.html")
            with open(hp, "w", encoding="utf-8") as fh:
                fh.write(html)
        return check_doc.run(p, hp, repo)


def codes(rep, level="fail"):
    return {i["code"] for i in rep.items if i["level"] == level}


class CheckDocTests(unittest.TestCase):
    def test_golden_passes(self):
        rep = run_text(golden_text(), GOOD_HTML)
        self.assertFalse(rep.failed(), rep.items)

    def test_banned_phrase(self):
        rep = run_text(golden_text().replace("## Summary\n", "## Summary\n\nThis is a robust design.\n"))
        self.assertIn("filler", codes(rep))

    def test_placeholder(self):
        rep = run_text(golden_text() + "\nTBD later.\n")
        self.assertIn("placeholder", codes(rep))

    def test_missing_front_matter_field(self):
        rep = run_text(golden_text().replace("owner: Platform team\n", "", 1))
        self.assertIn("front-matter", codes(rep))

    def test_citation_outside_file(self):
        rep = run_text(golden_text().replace("SessionManager.kt:13-19", "SessionManager.kt:13-900"))
        self.assertIn("citation", codes(rep))

    def test_citation_missing_file(self):
        rep = run_text(golden_text().replace("src/auth/TokenStore.kt:25-26", "src/auth/Nope.kt:25-26"))
        self.assertIn("citation", codes(rep))

    def test_snippet_not_verbatim(self):
        rep = run_text(golden_text().replace("maxRetries = 1", "maxRetries = 3"))
        self.assertIn("snippet", codes(rep))

    def test_snippet_without_caption(self):
        text = golden_text() + "\nSome code follows.\n\n```kotlin\nval x = 1\n```\n"
        rep = run_text(text)
        self.assertIn("snippet", codes(rep))

    def test_proposed_snippet_allowed(self):
        text = golden_text() + "\nProposed, not in repo:\n\n```kotlin\nval x = 1\n```\n"
        rep = run_text(text)
        self.assertNotIn("snippet", codes(rep))

    def test_unsupported_diagram(self):
        text = golden_text() + "\n*Figure 3. State*\n\n```mermaid\nstateDiagram-v2\n  [*] --> A\n```\n"
        rep = run_text(text)
        self.assertIn("diagram", codes(rep))

    def test_figure_never_referenced(self):
        rep = run_text(golden_text().replace("Figure 2 shows the same failure as a sequence.", "Same failure as a sequence."))
        self.assertIn("figure-ref", codes(rep))

    def test_requirement_unreferenced(self):
        text = golden_text().replace("Satisfies FR-1 only when", "Holds only when")
        text = text.replace("any change to NFR-1 or FR-1 targets", "any change to NFR-1 targets")
        rep = run_text(text)
        self.assertIn("traceability", codes(rep))

    def test_empty_section(self):
        rep = run_text(golden_text() + "\n## Rollout\n\n## Risks\n\nNone.\n")
        self.assertIn("empty-section", codes(rep))

    def test_untagged_document(self):
        text = golden_text().replace("Verified", "Checked").replace("Assumption", "Guess")
        rep = run_text(text)
        self.assertIn("evidence", codes(rep))

    def test_documented_but_recalled(self):
        text = golden_text() + "\nThe default quota is 600,000 per minute. Documented, recalled from memory, not re-checked.\n"
        self.assertIn("evidence", codes(run_text(text)))

    def test_html_external_script(self):
        bad = GOOD_HTML.replace("<body>", '<body><script src="https://cdn.example.com/m.js"></script>')
        self.assertIn("html-offline", codes(run_text(golden_text(), bad)))

    def test_html_external_stylesheet(self):
        bad = GOOD_HTML.replace("<head>", '<head><link rel="stylesheet" href="https://x.example/a.css">')
        self.assertIn("html-offline", codes(run_text(golden_text(), bad)))

    def test_html_remote_font_import(self):
        bad = GOOD_HTML.replace("<style>", "<style>@import url(https://fonts.example/f.css);")
        self.assertIn("html-offline", codes(run_text(golden_text(), bad)))

    def test_html_author_link_allowed(self):
        self.assertNotIn("html-offline", codes(run_text(golden_text(), GOOD_HTML)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
