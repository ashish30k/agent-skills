#!/usr/bin/env python3
"""Self-tests for build_docs.py. Standard library only.

Run: python3 -I scripts/test_build_docs.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_docs  # noqa: E402
import check_doc  # noqa: E402

GOLDEN = os.path.join(ROOT, "evals", "golden", "sample-session-architecture.md")
FIXTURE = os.path.join(ROOT, "evals", "fixtures", "sample-app")

C4 = """flowchart TD
  User[Customer] -->|uses| App
  subgraph System[Mobile app]
    App[App shell] -->|calls| Sess[SessionManager]
    Sess -->|reads, writes| Store([TokenStore])
    Sess -->|refresh| Api[AuthApi]
    App -->|renders| Ui[Screens]
  end
  Api -->|POST /v2/auth/refresh| Server[Auth server]
  Server -.->|audit event| Log([Audit log])
"""


def build(md, name="doc.md"):
    build_docs.WARNINGS.clear()
    return build_docs.build_html(md, name)


def doc(body, title="T"):
    return "---\ntitle: %s\n---\n\n# %s\n\n%s\n" % (title, title, body)


def flow(src):
    build_docs.WARNINGS.clear()
    return build_docs.render_flowchart([l for l in src.split("\n") if l.strip()], "Figure 1. test")


class BuildTests(unittest.TestCase):
    def test_golden_builds_and_passes_checker(self):
        with open(GOLDEN, encoding="utf-8") as fh:
            out = build(fh.read(), GOLDEN)
        self.assertEqual(build_docs.WARNINGS, [])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.html")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(out)
            rep = check_doc.run(GOLDEN, p, FIXTURE)
        self.assertFalse(rep.failed(), rep.items)
        self.assertEqual([i for i in rep.items if i["level"] == "warn"], [])

    def test_output_has_no_remote_references(self):
        with open(GOLDEN, encoding="utf-8") as fh:
            out = build(fh.read())
        for bad in ("http://", "https://", "<link", "@import", "<script src", "<img"):
            self.assertNotIn(bad, out)

    def test_deterministic(self):
        with open(GOLDEN, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(build(text), build(text))

    def test_one_h1_and_landmarks(self):
        out = build(doc("## A\n\ntext\n\n## B\n\ntext"))
        self.assertEqual(out.count("<h1"), 1)
        for part in ('<main id="content">', 'aria-label="Contents"', "prefers-color-scheme", "@media print", 'lang="en"'):
            self.assertIn(part, out)

    def test_html_in_text_and_code_is_escaped(self):
        out = build(doc("Use `<script>alert(1)</script>` here and a < b & c.\n\n```html\n<script>x()</script>\n```"))
        self.assertNotIn("<script>alert", out)
        self.assertNotIn("<script>x()", out)
        self.assertIn("&lt;script&gt;", out)

    def test_rejects_unsafe_or_unsupported_input(self):
        for body in ("[x](javascript:alert(1))", "![img](a.png)", "<div>raw</div>",
                     "| a | b |\n|---|---|\n| 1 |"):
            with self.assertRaises(build_docs.BuildError, msg=body):
                build(doc(body))

    def test_missing_title(self):
        with self.assertRaises(build_docs.BuildError):
            build("## Only a section\n\ntext")

    def test_cli_error_reports_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.md")
            with open(p, "w") as fh:
                fh.write(doc("fine\n\n[x](javascript:1)"))
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "build_docs.py"), p, "-o", os.path.join(d, "o.html")],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertRegex(r.stderr, r"bad\.md:\d+: error:")

    def test_captions_attach_and_callouts(self):
        md = doc("*Table 1. Sizes*\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n> **Warning:** careful\n\n"
                 "*Figure 1. Flow*\n\n```mermaid\nflowchart TD\n  A[a] -->|x| B[b]\n```")
        out = build(md)
        self.assertIn("<caption>Table 1. Sizes</caption>", out)
        self.assertIn("callout-warning", out)
        self.assertIn("<figcaption>Figure 1. Flow</figcaption>", out)

    def test_evidence_tags_styled(self):
        out = build(doc("Claim. **Verified**, `a.kt:1`. **Assumption:** x."))
        self.assertIn("tag-verified", out)
        self.assertIn("tag-assumption", out)

    def test_nested_lists(self):
        out = build(doc("- a\n  - b\n  - c\n- d\n\n1. one\n2. two"))
        self.assertEqual(out.count("<ul>"), 2)
        self.assertIn("<ol>", out)

    def test_highlight(self):
        h = build_docs.highlight("val x = 1 // c\n", "kotlin")
        self.assertIn('t-k">val', h)
        self.assertIn('t-n">1', h)
        self.assertIn("t-c", h)
        d = build_docs.highlight("+ added\n- removed\n@@ h", "diff")
        for cls in ("d-add", "d-del", "d-hunk"):
            self.assertIn(cls, d)


class DiagramTests(unittest.TestCase):
    def assert_clean(self, info):
        rects = list(info["nodes"].values())
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertFalse(build_docs.rects_overlap(a, b), "nodes overlap")
        for text, box in info["labels"]:
            for r in rects:
                self.assertFalse(build_docs.rects_overlap(box, r), "label '%s' overlaps a node" % text)

    def test_c4_layout_has_no_overlaps_or_warnings(self):
        _svg, info = flow(C4)
        self.assertEqual(build_docs.WARNINGS, [])
        self.assert_clean(info)
        self.assertEqual(len(info["labels"]), 7)

    def test_subgraph_contains_members_and_excludes_others(self):
        _svg, info = flow(C4)
        (gid, gbox), = info["groups"].items()
        for m in info["members"][gid]:
            n = info["nodes"][m]
            self.assertTrue(gbox[0] <= n[0] and gbox[1] <= n[1] and n[0] + n[2] <= gbox[0] + gbox[2] and n[1] + n[3] <= gbox[1] + gbox[3])
        for name in ("User", "Server", "Log"):
            self.assertFalse(build_docs.rects_overlap(gbox, info["nodes"][name]), name)

    def test_left_to_right(self):
        _svg, info = flow("flowchart LR\n  A[One] -->|go| B[Two]\n  B --> C[Three]")
        n = info["nodes"]
        self.assertLess(n["A"][0], n["B"][0])
        self.assertLess(n["B"][0], n["C"][0])
        self.assert_clean(info)

    def test_cycle_and_long_edge(self):
        _svg, info = flow("flowchart TD\n  A[a] --> B[b]\n  B --> C[c]\n  C --> A\n  A -->|skip| C\n  C --> D[d]")
        self.assert_clean(info)
        self.assertEqual(len(info["nodes"]), 4)

    def test_diamond_and_store_shapes(self):
        svg, _ = flow("flowchart TD\n  A{Ok?} -->|yes| B([Store])\n  A -->|no| C(Round)")
        self.assertIn("dg-diamond", svg)
        self.assertIn("dg-store", svg)

    def test_unsupported_diagrams_fail(self):
        for src in ("stateDiagram-v2\n  [*] --> A", "classDiagram\n  A <|-- B", "flowchart BT\n  A --> B"):
            with self.assertRaises(build_docs.BuildError, msg=src):
                build_docs.render_mermaid(src)
        with self.assertRaises(build_docs.BuildError):
            build_docs.render_mermaid("sequenceDiagram\n  A->>B: hi\n  alt x\n  B->>A: y\n  end")
        with self.assertRaises(build_docs.BuildError):
            build_docs.render_mermaid("flowchart TD\n  A --> A")

    def test_sequence_renders_messages_and_notes(self):
        svg = build_docs.render_mermaid(
            "sequenceDiagram\n  autonumber\n  participant S as SessionManager\n  S->>A: refresh(token)\n  A-->>S: null\n"
            "  Note over S,A: single attempt\n  S->>S: logout()", "Figure 2. x")
        self.assertIn("1. refresh(token)", svg)
        self.assertIn("dg-note", svg)
        self.assertIn("SessionManager", svg)
        self.assertEqual(svg.count("<svg"), 1)

    def test_unique_marker_ids(self):
        a = build_docs.render_mermaid("flowchart TD\n  A --> B")
        b = build_docs.render_mermaid("flowchart TD\n  A --> B")
        import re
        self.assertNotEqual(re.search(r'marker id="(\w+)"', a).group(1), re.search(r'marker id="(\w+)"', b).group(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
