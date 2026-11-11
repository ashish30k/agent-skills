#!/usr/bin/env python3
"""Mechanical quality checks for tech-doc-writer documents.

Standard library only. Works offline.

Usage:
  python3 -I check_doc.py DOC.md [--html DOC.html] [--repo ROOT] [--json]

Exit status: 0 when no hard failures, 1 otherwise. Warnings never fail the run.
"""
import argparse
import json
import os
import re
import sys

BANNED = [
    "in today's fast-paced", "it is important to note", "it's important to note",
    "this document aims to", "in conclusion", "robust", "seamless", "cutting-edge",
    "leverage", "empower", "streamline", "holistic", "best-in-class",
    "state-of-the-art", "world-class", "delve", "game-changer", "revolutionize",
    "obviously", "clearly,", "guarantees",
]
PLACEHOLDERS = [r"\bTODO\b", r"\bTBD\b", r"\bFIXME\b", r"\bXXX\b", r"lorem ipsum", r"\bfill in\b", r"\[insert"]
REQUIRED_FRONT = ["title", "type", "status", "owner", "date", "version", "source"]
CITE_RE = re.compile(r"`?((?:[\w.\-]+/)*[\w.\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`?")
FENCE_RE = re.compile(r"^(```|~~~)\s*([\w+\-]*)\s*$")
ELISION_RE = re.compile(r"^\s*(//|#|--|/\*)?\s*\.\.\.\s*(\*/)?\s*$")
EXT_URL_ATTR = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.I)


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, code, message, where=None):
        self.items.append({"level": level, "code": code, "message": message, "where": where})

    def hard(self, code, msg, where=None):
        self.add("fail", code, msg, where)

    def warn(self, code, msg, where=None):
        self.add("warn", code, msg, where)

    def failed(self):
        return any(i["level"] == "fail" for i in self.items)


def split_front_matter(text):
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            block = text[4:end]
            meta = {}
            for line in block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            body_start = text.find("\n", end + 1)
            offset = text[: body_start + 1].count("\n") if body_start != -1 else 0
            return meta, text[body_start + 1:] if body_start != -1 else "", offset
    return {}, text, 0


def parse_blocks(lines):
    """Return (blocks, prose_lines). blocks: dicts with lang, start, end, body, context."""
    blocks, prose, i = [], [], 0
    while i < len(lines):
        m = FENCE_RE.match(lines[i].rstrip())
        if m:
            fence, lang = m.group(1), m.group(2)
            j = i + 1
            body = []
            while j < len(lines) and not lines[j].startswith(fence):
                body.append(lines[j])
                j += 1
            ctx = " ".join(l for l in lines[max(0, i - 3):i] if l.strip())
            blocks.append({"lang": lang, "start": i + 1, "end": j + 1, "body": body, "context": ctx})
            i = j + 1
        else:
            prose.append((i + 1, lines[i]))
            i += 1
    return blocks, prose


def check_front_matter(meta, rep):
    if not meta:
        rep.hard("front-matter", "Missing front matter block.")
        return
    for key in REQUIRED_FRONT:
        if not any(k.startswith(key) for k in meta):
            rep.hard("front-matter", f"Front matter lacks '{key}'.")


def check_prose(prose, rep):
    for no, line in prose:
        low = line.lower()
        for phrase in BANNED:
            if phrase in low:
                rep.hard("filler", f"Banned phrase '{phrase}'.", f"line {no}")
        for pat in PLACEHOLDERS:
            if re.search(pat, line, re.I):
                rep.hard("placeholder", f"Placeholder text matches {pat}.", f"line {no}")
        if "!" in line and not line.lstrip().startswith(("|", ">")) and "`" not in line:
            rep.warn("style", "Exclamation mark.", f"line {no}")
        if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", line):
            rep.warn("style", "Emoji in document.", f"line {no}")


def check_empty_sections(prose, rep):
    heads = [(no, l) for no, l in prose if re.match(r"^#{1,6}\s", l)]
    lines = dict(prose)
    all_nos = sorted(lines)
    for idx, (no, head) in enumerate(heads):
        nxt = heads[idx + 1][0] if idx + 1 < len(heads) else (all_nos[-1] + 1 if all_nos else no + 1)
        content = [lines[n] for n in all_nos if no < n < nxt and lines[n].strip()]
        has_child = idx + 1 < len(heads) and len(re.match(r"^(#+)", heads[idx + 1][1]).group(1)) > len(re.match(r"^(#+)", head).group(1))
        if not content and not has_child:
            rep.hard("empty-section", f"Section has no content: {head.strip()}", f"line {no}")


def check_citations(text, repo, rep):
    cites = set()
    for m in CITE_RE.finditer(text):
        path, a, b = m.group(1), int(m.group(2)), m.group(3)
        if "/" not in path and "." not in path:
            continue
        cites.add((path, a, int(b) if b else a))
    if not repo:
        if cites:
            rep.warn("citation", "Citations present but --repo not given; not verified.")
        return cites
    for path, a, b in sorted(cites):
        full = os.path.join(repo, path)
        if not os.path.isfile(full):
            continue_hint = path.split("/")[-1]
            if re.match(r"^\d+\.\d+", continue_hint):
                continue
            rep.hard("citation", f"Cited file not found: {path}")
            continue
        with open(full, encoding="utf-8", errors="replace") as fh:
            n = sum(1 for _ in fh)
        if a < 1 or b < a or b > n:
            rep.hard("citation", f"Line range {a}-{b} outside {path} ({n} lines).")
    return cites


def norm(s):
    return re.sub(r"\s+", " ", s.strip())


def check_snippets(blocks, repo, rep):
    for blk in blocks:
        if blk["lang"] in ("mermaid", "diff", "text", ""):
            continue
        ctx = blk["context"]
        if re.search(r"proposed|pseudocode|pseudo-code|example output|illustrative", ctx, re.I):
            continue
        m = CITE_RE.search(ctx)
        if not m:
            if blk["lang"] in ("kotlin", "java", "swift", "python", "javascript", "typescript", "js", "ts", "kt", "go", "rust"):
                rep.hard("snippet", "Code block has no file:line caption and is not labeled Proposed or Pseudocode.", f"line {blk['start']}")
            continue
        if not repo:
            continue
        path, a = m.group(1), int(m.group(2))
        b = int(m.group(3)) if m.group(3) else a
        full = os.path.join(repo, path)
        if not os.path.isfile(full):
            continue
        with open(full, encoding="utf-8", errors="replace") as fh:
            src = [norm(l) for l in fh.read().splitlines()[a - 1:b]]
        src = [l for l in src if l]
        snippet = [l for l in blk["body"]]
        pos, ok = 0, True
        wildcard = False
        for raw in snippet:
            if ELISION_RE.match(raw):
                wildcard = True
                continue
            line = norm(raw)
            if not line:
                continue
            if wildcard:
                while pos < len(src) and src[pos] != line:
                    pos += 1
                wildcard = False
            if pos >= len(src) or src[pos] != line:
                ok = False
                rep.hard("snippet", f"Snippet differs from {path}:{a}-{b} near: {line[:60]}", f"line {blk['start']}")
                break
            pos += 1
        if ok and not any(not ELISION_RE.match(l) for l in snippet):
            rep.warn("snippet", "Empty snippet.", f"line {blk['start']}")


def check_mermaid(blocks, text, rep):
    figs_defined = set(int(n) for n in re.findall(r"^\*?\*?Figure\s+(\d+)\b", text, re.M))
    for blk in blocks:
        if blk["lang"] != "mermaid":
            continue
        body = [l for l in blk["body"] if l.strip() and not l.strip().startswith("%%")]
        if not body:
            rep.hard("diagram", "Empty mermaid block.", f"line {blk['start']}")
            continue
        head = body[0].strip()
        if not (head.startswith("sequenceDiagram") or re.match(r"^(flowchart|graph)\s+(TD|TB|LR|RL|BT)\b", head)):
            rep.hard("diagram", f"Unsupported diagram type: {head.split()[0]}. Use sequenceDiagram or flowchart.", f"line {blk['start']}")
            continue
        if head.startswith("sequenceDiagram"):
            if not any("->>" in l or "-->>" in l for l in body[1:]):
                rep.hard("diagram", "Sequence diagram has no messages.", f"line {blk['start']}")
        else:
            opens = sum(1 for l in body if l.strip().startswith("subgraph"))
            closes = sum(1 for l in body if l.strip() == "end")
            if opens != closes:
                rep.hard("diagram", "Unbalanced subgraph/end.", f"line {blk['start']}")
            nodes = set(re.findall(r"\b([A-Za-z][\w]*)\s*(?:\[|\(|\{)", "\n".join(body)))
            if len(nodes) > 14:
                rep.warn("diagram", f"{len(nodes)} nodes; split the figure.", f"line {blk['start']}")
            edges = [l for l in body if re.search(r"-->|-\.->|==>", l)]
            if edges and not any("|" in l for l in edges):
                rep.warn("diagram", "No labeled edges.", f"line {blk['start']}")
        near = " ".join(blk["context"].split()) + " " + " ".join(
            l for l in text.splitlines()[blk["end"]:blk["end"] + 3])
        if not re.search(r"Figure\s+\d+", near):
            rep.hard("diagram", "Diagram has no 'Figure N' caption next to it.", f"line {blk['start']}")
    for n in sorted(figs_defined):
        refs = len(re.findall(rf"\bFigure\s+{n}\b", text))
        if refs < 2:
            rep.hard("figure-ref", f"Figure {n} is captioned but never referenced in the text.")


def check_requirements(text, rep):
    ids = set(re.findall(r"\b((?:N?FR)-\d+)\b", text))
    for rid in sorted(ids):
        if len(re.findall(rf"\b{re.escape(rid)}\b", text)) < 2:
            rep.hard("traceability", f"{rid} is defined but never referenced again.")


def check_documented_recall(prose, rep):
    """A claim tagged Documented must not be one recalled from memory."""
    for no, line in prose:
        if re.search(r"\bDocumented\b", line) and re.search(
                r"recalled|from memory|not re-?checked|unverified recall", line, re.I):
            rep.hard("evidence", "Claim tagged Documented but described as recalled or unchecked; tag it Assumption.", f"line {no}")


def check_tags(text, repo, rep):
    verified = len(re.findall(r"\*\*Verified\*\*|\bVerified\b", text))
    documented = len(re.findall(r"\bDocumented\b", text))
    assumption = len(re.findall(r"\bAssumption\b", text))
    if repo and verified == 0:
        rep.warn("evidence", "Source repo given but no claim is tagged Verified.")
    if verified + documented + assumption == 0:
        rep.hard("evidence", "No claim is tagged Verified, Documented, or Assumption.")


def check_html(path, rep):
    with open(path, encoding="utf-8", errors="replace") as fh:
        html = fh.read()
    scrub = re.sub(r"<a\b[^>]*>", "<a>", html, flags=re.I)
    if re.search(r"<script[^>]+src\s*=", scrub, re.I):
        rep.hard("html-offline", "External <script src> found.")
    if re.search(r"<link\b[^>]*>", scrub, re.I):
        rep.hard("html-offline", "<link> element found; styles must be inline.")
    if re.search(r"<img\b[^>]+src\s*=\s*[\"']\s*(?:https?:)?//", scrub, re.I):
        rep.hard("html-offline", "Remote image found.")
    if re.search(r"@import|url\(\s*[\"']?\s*(?:https?:)?//", scrub, re.I):
        rep.hard("html-offline", "CSS @import or remote url() found.")
    if re.search(r"<(iframe|embed|object)\b", scrub, re.I):
        rep.hard("html-offline", "iframe/embed/object found.")
    if not re.search(r"<html[^>]+lang\s*=", html, re.I):
        rep.warn("html-a11y", "<html> has no lang attribute.")
    if not re.search(r"<title>.+</title>", html, re.I | re.S):
        rep.warn("html-a11y", "No <title>.")
    if len(re.findall(r"<h1\b", html, re.I)) != 1:
        rep.warn("html-a11y", "Expected exactly one <h1>.")
    if "prefers-color-scheme" not in html:
        rep.warn("html-theme", "No dark-mode support (prefers-color-scheme).")
    if "@media print" not in html:
        rep.warn("html-print", "No print stylesheet.")
    if "<svg" in html and not re.search(r"<svg[^>]+role\s*=\s*[\"']img", html, re.I):
        rep.warn("html-a11y", "SVG without role=img.")


def run(doc, html, repo):
    rep = Report()
    with open(doc, encoding="utf-8") as fh:
        text = fh.read()
    meta, body, offset = split_front_matter(text)
    lines = body.splitlines()
    blocks, prose = parse_blocks(lines)
    check_front_matter(meta, rep)
    check_prose(prose, rep)
    check_empty_sections(prose, rep)
    prose_text = "\n".join(l for _, l in prose)
    check_citations(prose_text + "\n" + "\n".join(b["context"] for b in blocks), repo, rep)
    check_snippets(blocks, repo, rep)
    check_mermaid(blocks, body, rep)
    check_requirements(body, rep)
    check_documented_recall(prose, rep)
    check_tags(body, repo, rep)
    if html:
        check_html(html, rep)
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("doc")
    ap.add_argument("--html")
    ap.add_argument("--repo")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = run(args.doc, args.html, args.repo)
    if args.json:
        print(json.dumps({"failed": rep.failed(), "items": rep.items}, indent=2))
    else:
        for i in rep.items:
            loc = f" ({i['where']})" if i["where"] else ""
            print(f"[{i['level'].upper()}] {i['code']}: {i['message']}{loc}")
        fails = sum(1 for i in rep.items if i["level"] == "fail")
        warns = sum(1 for i in rep.items if i["level"] == "warn")
        print(f"\n{'FAIL' if fails else 'PASS'}: {fails} hard failures, {warns} warnings")
    return 1 if rep.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
