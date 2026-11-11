#!/usr/bin/env python3
"""Build one self-contained, offline HTML file from a tech-doc-writer Markdown document.

Standard library only. No network. Same input gives the same bytes.

Usage:
  python3 -I build_docs.py DOC.md -o OUT.html [--strict]

Supported Markdown: front matter, headings, paragraphs, nested lists, tables,
fenced code, blockquotes (with Note/Warning/Decision/Assumption callouts),
inline code, bold, italic, links, horizontal rules, and `Figure N.` / `Table N.`
caption lines. Mermaid blocks: `sequenceDiagram` and `flowchart` (see SKILL.md).

Anything outside this subset stops the build with a file:line message.
Layout problems in diagrams print warnings; --strict turns them into errors.
"""
import argparse
import html
import textwrap
import math
import os
import re
import sys


class BuildError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg)
        self.line = line


WARNINGS = []
CURRENT = [None]


def warn(msg, line=None):
    WARNINGS.append((line if line is not None else CURRENT[0], msg))


def esc(s):
    return html.escape(s, quote=True)


# ---------------------------------------------------------------- inline text

TAG_WORDS = {"Verified": "verified", "Documented": "documented", "Assumption": "assumption"}
LINK_OK = re.compile(r"^(https?://\S+|#[\w\-]*|mailto:\S+|[\w./\-]+(#[\w\-]*)?)$")


def inline(text):
    if re.search(r"!\[", text):
        raise BuildError("images are not supported (the output must not load anything)")
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    t = re.sub(r"`([^`]+)`", stash, text)
    t = esc(t)

    def link(m):
        label, url = m.group(1), html.unescape(m.group(2))
        if not LINK_OK.match(url) or re.match(r"(javascript|data|vbscript):", url, re.I):
            raise BuildError("unsupported link target: " + url)
        return '<a href="%s">%s</a>' % (esc(url), label)

    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, t)

    def bold(m):
        inner = m.group(1)
        key = inner.rstrip(":,.")
        if key in TAG_WORDS:
            return '<strong class="tag tag-%s">%s</strong>' % (TAG_WORDS[key], inner)
        return "<strong>%s</strong>" % inner

    t = re.sub(r"\*\*(.+?)\*\*", bold, t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: "<code>%s</code>" % esc(codes[int(m.group(1))]), t)
    return t


def plain(text):
    """Text with markdown markers removed, for labels and slugs."""
    t = re.sub(r"`([^`]+)`", r"\1", text)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<![\w*])\*(.+?)\*(?![\w*])", r"\1", t)
    return t.strip()


def slugify(text, used):
    s = re.sub(r"[^a-z0-9]+", "-", plain(text).lower()).strip("-") or "section"
    base, n = s, 2
    while s in used:
        s = "%s-%d" % (base, n)
        n += 1
    used.add(s)
    return s


# ------------------------------------------------------------ markdown blocks

FENCE = re.compile(r"^(```|~~~)\s*([\w+\-]*)\s*$")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
CAPTION = re.compile(r"^(?:\*\*|\*|_)?(Figure|Table)\s+(\d+)[.:]\s*(.*?)(?:\*\*|\*|_)?$")
CAPTION_BOLD = re.compile(r"^\*\*(Figure|Table)\s+(\d+)[.:]?\*\*\s*(.*)$")


def split_front_matter(text):
    if not text.startswith("---\n"):
        return {}, text, 0
    end = text.find("\n---", 4)
    if end == -1:
        raise BuildError("front matter is not closed", 1)
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    nl = text.find("\n", end + 1)
    body = text[nl + 1:] if nl != -1 else ""
    offset = text[: nl + 1].count("\n") if nl != -1 else text.count("\n")
    return meta, body, offset


def split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], "", 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if s[i] == "|":
            cells.append(cur.strip())
            cur = ""
        else:
            cur += s[i]
        i += 1
    cells.append(cur.strip())
    return cells


def is_block_start(line):
    return bool(
        FENCE.match(line.rstrip())
        or re.match(r"^#{1,6}\s", line)
        or line.startswith(">")
        or LIST_ITEM.match(line)
        or re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line)
        or line.lstrip().startswith("|")
    )


def parse_blocks(lines, base):
    blocks, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        ln = base + i + 1
        if not line.strip():
            i += 1
            continue
        m = FENCE.match(line.rstrip())
        if m:
            fence, lang = m.group(1), m.group(2)
            j, body = i + 1, []
            while j < n and not lines[j].startswith(fence):
                body.append(lines[j])
                j += 1
            if j >= n:
                raise BuildError("code fence is not closed", ln)
            blocks.append({"t": "code", "lang": lang, "text": "\n".join(body), "line": ln})
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            blocks.append({"t": "heading", "level": len(m.group(1)), "text": m.group(2), "line": ln})
            i += 1
            continue
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            blocks.append({"t": "hr", "line": ln})
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < n and TABLE_SEP.match(lines[i + 1]):
            head = split_row(line)
            aligns = []
            for c in split_row(lines[i + 1]):
                c = c.strip()
                aligns.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "")
            j, rows = i + 2, []
            while j < n and lines[j].strip().startswith("|"):
                row = split_row(lines[j])
                if len(row) != len(head):
                    raise BuildError("table row has %d cells, header has %d" % (len(row), len(head)), base + j + 1)
                rows.append(row)
                j += 1
            blocks.append({"t": "table", "head": head, "aligns": aligns, "rows": rows, "line": ln})
            i = j
            continue
        if line.startswith(">"):
            j, q = i, []
            while j < n and lines[j].startswith(">"):
                q.append(re.sub(r"^>\s?", "", lines[j]))
                j += 1
            blocks.append({"t": "quote", "lines": q, "line": ln})
            i = j
            continue
        if LIST_ITEM.match(line):
            items, j = [], i
            while j < n:
                lm = LIST_ITEM.match(lines[j])
                if lm:
                    items.append([len(lm.group(1).expandtabs(4)), lm.group(2)[0].isdigit(), lm.group(3)])
                    j += 1
                elif lines[j].strip() and lines[j].startswith((" ", "\t")) and items:
                    items[-1][2] += " " + lines[j].strip()
                    j += 1
                else:
                    break
            blocks.append({"t": "list", "items": items, "line": ln})
            i = j
            continue
        if re.match(r"^\s*<[A-Za-z!/]", line):
            raise BuildError("raw HTML is not supported; use Markdown", ln)
        j, para = i, []
        while j < n and lines[j].strip() and not (j > i and is_block_start(lines[j])):
            para.append(lines[j].strip())
            j += 1
        blocks.append({"t": "para", "text": " ".join(para), "line": ln})
        i = j
    return blocks


def attach_captions(blocks):
    out, i = [], 0
    caps = {}
    for idx, b in enumerate(blocks):
        if b["t"] == "para":
            m = CAPTION_BOLD.match(b["text"]) or CAPTION.match(b["text"])
            if m:
                caps[idx] = (m.group(1), int(m.group(2)), m.group(3))

    def target(b):
        return b["t"] == "table" or (b["t"] == "code" and b["lang"] == "mermaid")

    used = set()
    for kind, want in (("Figure", "mermaid"), ("Table", "table")):
        mine = sorted(((num, idx, text) for idx, (k, num, text) in caps.items() if k == kind))
        tgt = [i for i, b in enumerate(blocks) if (b["t"] == "table" if want == "table" else (b["t"] == "code" and b["lang"] == "mermaid"))]
        if mine and len(mine) == len(tgt) and [m[1] for m in mine] == sorted(m[1] for m in mine):
            for (num, idx, text), ti in zip(mine, tgt):
                blocks[ti]["caption"] = (kind, num, text)
                used.add(idx)
    for idx, (kind, num, text) in caps.items():
        if idx in used:
            continue
        want = "table" if kind == "Table" else "mermaid"

        def fits(b):
            return b["t"] == "table" if want == "table" else (b["t"] == "code" and b["lang"] == "mermaid")

        for cand in (idx + 1, idx - 1):
            if 0 <= cand < len(blocks) and fits(blocks[cand]) and cand not in used and "caption" not in blocks[cand]:
                blocks[cand]["caption"] = (kind, num, text)
                used.add(idx)
                break
    return [b for idx, b in enumerate(blocks) if idx not in used]


# ---------------------------------------------------------- code highlighting

KEYWORDS = {
    "kotlin": "val var fun class object interface data sealed enum when if else for while do return break continue try catch finally throw import package private public protected internal override open abstract suspend inline companion is as in null true false this super by init constructor typealias lateinit const operator reified out where",
    "java": "class interface enum extends implements new return if else for while do break continue try catch finally throw throws import package private public protected static final abstract void int long double float boolean char byte short null true false this super instanceof synchronized volatile switch case default var record",
    "swift": "let var func class struct enum protocol extension import return if else guard switch case default for while in repeat break continue try catch throw throws async await private public internal fileprivate open static final init self super nil true false where as is",
    "javascript": "const let var function class extends return if else for while do break continue try catch finally throw import export from default new this super null undefined true false typeof instanceof async await switch case of in yield static get set",
    "go": "func var const type struct interface package import return if else for range switch case default break continue go defer chan map select nil true false",
    "rust": "fn let mut const struct enum impl trait use mod pub return if else match for while loop break continue as in ref self Self true false where async await move unsafe",
    "python": "def class return if elif else for while break continue try except finally raise import from as with pass lambda yield None True False and or not in is global nonlocal async await assert del",
    "bash": "if then else elif fi for while do done case esac function in return exit export local set unset echo cd read source",
    "sql": "select from where group by order having join left right inner outer on as and or not null insert into values update set delete create table index primary key foreign references alter drop add column distinct union all limit offset case when then else end exists in is like between",
}
ALIASES = {"kt": "kotlin", "kts": "kotlin", "js": "javascript", "ts": "javascript", "typescript": "javascript",
           "jsx": "javascript", "tsx": "javascript", "sh": "bash", "shell": "bash", "zsh": "bash", "py": "python",
           "c": "java", "cpp": "java", "csharp": "java", "cs": "java", "gradle": "kotlin", "yml": "yaml",
           "html": "xml", "proto": "java"}
CLIKE = ("kotlin", "java", "swift", "javascript", "go", "rust")
STR_DQ = r'"(?:\\.|[^"\\\n])*"'
STR_SQ = r"'(?:\\.|[^'\\\n])*'"
STR_BT = r"`(?:\\.|[^`\\])*`"
NUM = r"\b(?:0x[0-9A-Fa-f_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?[LlFfuU]?)\b"
IDENT = r"[A-Za-z_]\w*"
_PAT_CACHE = {}


def token_pattern(lang):
    if lang in _PAT_CACHE:
        return _PAT_CACHE[lang]
    if lang in CLIKE:
        parts = [("c", r"/\*.*?\*/|//[^\n]*"), ("s", r'"""[\s\S]*?"""|' + STR_DQ + "|" + STR_SQ + "|" + STR_BT),
                 ("a", r"@\w+"), ("n", NUM), ("i", IDENT)]
    elif lang == "python":
        parts = [("c", r"#[^\n]*"), ("s", r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|' + STR_DQ + "|" + STR_SQ),
                 ("a", r"@\w+"), ("n", NUM), ("i", IDENT)]
    elif lang == "bash":
        parts = [("c", r"(?<![\w$])#[^\n]*"), ("s", STR_DQ + "|" + STR_SQ),
                 ("v", r"\$\{[^}]*\}|\$\w+"), ("n", NUM), ("i", IDENT)]
    elif lang == "sql":
        parts = [("c", r"--[^\n]*|/\*.*?\*/"), ("s", r"'(?:''|[^'])*'"), ("n", NUM), ("i", IDENT)]
    elif lang == "json":
        parts = [("p", r'"(?:\\.|[^"\\\n])*"(?=\s*:)'), ("s", STR_DQ), ("n", r"-?\b\d[\d.eE+-]*\b"), ("i", IDENT)]
    elif lang == "yaml":
        parts = [("c", r"#[^\n]*"), ("p", r"^[ \t-]*[\w.\-/]+(?=\s*:)"), ("s", STR_DQ + "|" + STR_SQ), ("n", NUM), ("i", IDENT)]
    elif lang == "xml":
        parts = [("c", r"<!--[\s\S]*?-->"), ("k", r"</?[\w:\-]+|/?>"), ("s", STR_DQ + "|" + STR_SQ), ("p", r"[\w:\-]+(?==)")]
    else:
        parts = []
    rx = re.compile("|".join("(?P<%s>%s)" % (k, v) for k, v in parts), re.M) if parts else None
    _PAT_CACHE[lang] = rx
    return rx


def highlight(code, lang):
    lang = ALIASES.get(lang.lower(), lang.lower())
    if lang == "diff":
        out = []
        for line in code.split("\n"):
            cls = "d-add" if line.startswith("+") and not line.startswith("+++") else \
                  "d-del" if line.startswith("-") and not line.startswith("---") else \
                  "d-hunk" if line.startswith("@@") else ""
            out.append('<span class="%s">%s</span>' % (cls, esc(line)) if cls else esc(line))
        return "\n".join(out)
    rx = token_pattern(lang)
    if rx is None:
        return esc(code)
    kw = set(KEYWORDS.get(lang, "").split())
    if lang == "sql":
        kw = set(kw)
    out, pos = [], 0
    for m in rx.finditer(code):
        out.append(esc(code[pos:m.start()]))
        kind, tok = m.lastgroup, m.group()
        if kind == "i":
            low = tok.lower() if lang == "sql" else tok
            if low in kw or tok in ("true", "false", "null") and lang in ("json", "yaml"):
                kind = "k"
            elif tok[:1].isupper() and lang in CLIKE + ("python",):
                kind = "t"
            else:
                out.append(esc(tok))
                pos = m.end()
                continue
        out.append('<span class="t-%s">%s</span>' % (kind, esc(tok)))
        pos = m.end()
    out.append(esc(code[pos:]))
    return "".join(out)


# ----------------------------------------------------------------- diagrams

FONT = 13
CHAR_W = 7.3
_MARK = [0]


def next_marker():
    _MARK[0] += 1
    return "ah%d" % _MARK[0]


def label_lines(text, width=26):
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    lines = []
    for part in re.split(r"<br\s*/?>", text):
        words, cur = part.split(), ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > width:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
    return lines or [""]


def tw(s):
    return len(s) * CHAR_W


def multi_text(lines, cx, cy, cls="dg-text", lh=16):
    y0 = cy - (len(lines) - 1) * lh / 2
    parts = ['<text class="%s" x="%.1f" y="%.1f" text-anchor="middle" dominant-baseline="central">' % (cls, cx, y0)]
    for i, ln in enumerate(lines):
        parts.append('<tspan x="%.1f" dy="%s">%s</tspan>' % (cx, "0" if i == 0 else str(lh), esc(ln)))
    parts.append("</text>")
    return "".join(parts)


def rects_overlap(a, b, pad=0):
    return not (a[0] + a[2] + pad <= b[0] or b[0] + b[2] + pad <= a[0] or
                a[1] + a[3] + pad <= b[1] or b[1] + b[3] + pad <= a[1])


# ---- flowchart parsing

NODE_PATTERNS = [
    (re.compile(r"([A-Za-z_]\w*)\(\[(.*?)\]\)"), "store"),
    (re.compile(r"([A-Za-z_]\w*)\[(.*?)\]"), "rect"),
    (re.compile(r"([A-Za-z_]\w*)\((.*?)\)"), "round"),
    (re.compile(r"([A-Za-z_]\w*)\{(.*?)\}"), "diamond"),
    (re.compile(r"([A-Za-z_]\w*)"), "bare"),
]
EDGE_OP = re.compile(r"\s*(-->|-\.->|==>)\s*(?:\|([^|]*)\|)?\s*")


class FlowNode:
    def __init__(self, nid):
        self.id, self.label, self.shape, self.group, self.order = nid, nid, "rect", (), 0
        self.labeled = False


def parse_flowchart(src):
    head = src[0].strip()
    m = re.match(r"^(?:flowchart|graph)\s+(TD|TB|LR)\s*$", head)
    if not m:
        raise BuildError("flowchart direction must be TD or LR (BT and RL are not supported): " + head)
    direction = "LR" if m.group(1) == "LR" else "TD"
    nodes, edges, groups, stack = {}, [], {}, []

    def get_node(nid):
        if nid not in nodes:
            nodes[nid] = FlowNode(nid)
            nodes[nid].order = len(nodes)
        return nodes[nid]

    def parse_node(line, pos):
        for rx, shape in NODE_PATTERNS:
            m2 = rx.match(line, pos)
            if m2:
                n = get_node(m2.group(1))
                if shape != "bare":
                    n.shape, n.label, n.labeled = shape, m2.group(2), True
                if stack and not n.group:
                    n.group = tuple(stack)
                return n, m2.end()
        raise BuildError("cannot read a node at: " + line[pos:pos + 30])

    for raw in src[1:]:
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if line.startswith("subgraph"):
            m2 = re.match(r"^subgraph\s+([\w\-]+)(?:\s*\[(.*)\])?\s*$", line) or \
                 re.match(r"^subgraph\s+(.+?)\s*$", line)
            if not m2:
                raise BuildError("cannot read: " + line)
            gid = re.sub(r"\W+", "_", m2.group(1)) + "_%d" % len(groups)
            title = (m2.group(2) if m2.lastindex and m2.lastindex >= 2 and m2.group(2) else m2.group(1))
            groups[gid] = {"title": title.strip().strip('"'), "parent": stack[-1] if stack else None, "order": len(groups)}
            stack.append(gid)
            continue
        if line == "end":
            if not stack:
                raise BuildError("'end' without subgraph")
            stack.pop()
            continue
        if re.match(r"^(classDef|class|style|linkStyle|click|direction)\b", line):
            warn("diagram styling line ignored: " + line.split()[0])
            continue
        n, pos = parse_node(line, 0)
        while True:
            m2 = EDGE_OP.match(line, pos)
            if not m2:
                break
            n2, pos = parse_node(line, m2.end())
            edges.append({"s": n.id, "d": n2.id, "style": {"-->": "solid", "-.->": "dashed", "==>": "thick"}[m2.group(1)],
                          "label": (m2.group(2) or "").strip()})
            n = n2
        if line[pos:].strip():
            raise BuildError("unsupported flowchart syntax near: " + line[pos:].strip()[:40])
    if stack:
        raise BuildError("subgraph is not closed")
    if not nodes:
        raise BuildError("flowchart has no nodes")
    return direction, nodes, edges, groups


# ---- flowchart layout

NODE_GAP = 38
EDGE_GAP = 16
GROUP_PAD = 22
LAYER_GAP_TD = 80
LAYER_GAP_LR = 120


def size_nodes(nodes):
    for n in nodes.values():
        n.lines = label_lines(n.label)
        tw_max = max(tw(l) for l in n.lines)
        h = 18 * len(n.lines) + 22
        w = max(80, tw_max + 34)
        if n.shape == "diamond":
            w, h = max(110, tw_max * 1.45 + 40), max(64, h + 24)
        elif n.shape == "store":
            h += 12
        n.w, n.h = w, max(42, h)


def layout_flowchart(direction, nodes, edges, groups):
    size_nodes(nodes)
    ids = list(nodes)
    if len(ids) > 40:
        warn("flowchart has %d nodes; split it into several figures" % len(ids))
    for e in edges:
        if e["s"] == e["d"]:
            raise BuildError("self-loop on %s is not supported" % e["s"])
    # break cycles
    adj = {i: [] for i in ids}
    for k, e in enumerate(edges):
        adj[e["s"]].append((e["d"], k))
    state, rev = {}, set()

    def dfs(u):
        state[u] = 1
        for v, k in adj[u]:
            if state.get(v) == 1:
                rev.add(k)
            elif v not in state:
                dfs(v)
        state[u] = 2

    for i in ids:
        if i not in state:
            dfs(i)
    oriented = []
    for k, e in enumerate(edges):
        oriented.append((e["d"], e["s"], k) if k in rev else (e["s"], e["d"], k))
    layer = {i: 0 for i in ids}
    for _ in range(len(ids) + 1):
        changed = False
        for u, v, _k in oriented:
            if layer[v] < layer[u] + 1:
                layer[v] = layer[u] + 1
                changed = True
        if not changed:
            break
    has_in = {v for _u, v, _k in oriented}
    for i in ids:
        outs = [layer[v] for u, v, _k in oriented if u == i]
        if i not in has_in and outs:
            layer[i] = min(outs) - 1
    low = min(layer.values())
    for i in ids:
        layer[i] -= low
    # chains with dummies
    chains, glob = [], {i: nodes[i].group for i in ids}
    for u, v, k in oriented:
        chain = [u]
        gu, gv = nodes[u].group, nodes[v].group
        common = []
        for a, b in zip(gu, gv):
            if a != b:
                break
            common.append(a)
        for lv in range(layer[u] + 1, layer[v]):
            d = "~%d_%d" % (k, lv)
            layer[d] = lv
            glob[d] = tuple(common)
            chain.append(d)
        chain.append(v)
        chains.append((chain, k, k in rev))
    nlayers = max(layer.values()) + 1
    layers = [[] for _ in range(nlayers)]
    for i in ids:
        layers[layer[i]].append(i)
    for chain, _k, _r in chains:
        for d in chain[1:-1]:
            layers[layer[d]].append(d)
    up, down = {}, {}
    for chain, _k, _r in chains:
        for a, b in zip(chain, chain[1:]):
            down.setdefault(a, []).append(b)
            up.setdefault(b, []).append(a)

    def positions():
        return {n: i for ly in layers for i, n in enumerate(ly)}

    def order_layer(ly, bary):
        def rec(items, depth):
            clusters, seq = {}, []
            for n in items:
                g = glob[n]
                if len(g) > depth:
                    if g[depth] not in clusters:
                        clusters[g[depth]] = []
                        seq.append(("g", g[depth]))
                    clusters[g[depth]].append(n)
                else:
                    seq.append(("n", n))
            ranked = []
            for idx, (kind, ref) in enumerate(seq):
                if kind == "n":
                    ranked.append((bary[ref], idx, [ref]))
                else:
                    mem = clusters[ref]
                    ranked.append((sum(bary[x] for x in mem) / len(mem), idx, rec(mem, depth + 1)))
            ranked.sort(key=lambda t: (t[0], t[1]))
            return [x for _b, _i, lst in ranked for x in lst]
        return rec(ly, 0)

    for _ in range(12):
        for sweep in (0, 1):
            seq = range(1, nlayers) if sweep == 0 else range(nlayers - 2, -1, -1)
            nbmap = up if sweep == 0 else down
            for li in seq:
                pos = positions()
                bary = {}
                for n in layers[li]:
                    ns = nbmap.get(n, [])
                    bary[n] = sum(pos[x] for x in ns) / len(ns) if ns else pos[n]
                layers[li] = order_layer(layers[li], bary)
    horiz = direction == "LR"

    def csize(n):
        if n.startswith("~"):
            return 8
        return nodes[n].h if horiz else nodes[n].w

    def msize(n):
        if n.startswith("~"):
            return 0
        return nodes[n].w if horiz else nodes[n].h

    def sep(a, b):
        base = (csize(a) + csize(b)) / 2
        if a.startswith("~") or b.startswith("~"):
            gap = EDGE_GAP
        else:
            gap = NODE_GAP
        ga, gb = set(glob[a]), set(glob[b])
        return base + gap + GROUP_PAD * len(ga ^ gb)

    cross = {}
    for ly in layers:
        x = 0.0
        for j, n in enumerate(ly):
            if j:
                x += sep(ly[j - 1], n)
            cross[n] = x
        mid = (cross[ly[0]] + cross[ly[-1]]) / 2
        for n in ly:
            cross[n] -= mid

    def resolve(ly, desired):
        k = len(ly)
        left, right = [0.0] * k, [0.0] * k
        left[0] = desired[0]
        for j in range(1, k):
            left[j] = max(desired[j], left[j - 1] + sep(ly[j - 1], ly[j]))
        right[-1] = desired[-1]
        for j in range(k - 2, -1, -1):
            right[j] = min(desired[j], right[j + 1] - sep(ly[j], ly[j + 1]))
        return [(a + b) / 2 for a, b in zip(left, right)]

    for it in range(8):
        order_ = list(range(1, nlayers)) if it % 2 == 0 else list(range(nlayers - 2, -1, -1))
        for li in order_:
            ly = layers[li]
            nbmap = up if it % 2 == 0 else down
            desired = []
            for n in ly:
                ns = nbmap.get(n, [])
                desired.append(sum(cross[x] for x in ns) / len(ns) if ns else cross[n])
            for n, v in zip(ly, resolve(ly, desired)):
                cross[n] = v
    # keep subgraph boxes free of non-member nodes
    gmembers = {}
    for n, g in glob.items():
        for gid in g:
            gmembers.setdefault(gid, []).append(n)
    kids = {}
    for gid, info in groups.items():
        if info["parent"]:
            kids.setdefault(info["parent"], []).append(gid)

    def below(gid):
        return 1 + max([below(c) for c in kids.get(gid, [])], default=0)

    for _ in range(24):
        moved = False
        for gid, mem in gmembers.items():
            lo = min(cross[m] - csize(m) / 2 for m in mem)
            hi = max(cross[m] + csize(m) / 2 for m in mem)
            lmin, lmax = min(layer[m] for m in mem), max(layer[m] for m in mem)
            pad = 12 + 10 * (below(gid) - 1) + 8
            lo_pad = pad + (22 if horiz else 0)
            for li in range(lmin, lmax + 1):
                for n in layers[li]:
                    if gid in glob[n]:
                        continue
                    half = csize(n) / 2
                    if cross[n] + half + NODE_GAP / 2 > lo - lo_pad and cross[n] - half - NODE_GAP / 2 < hi + pad:
                        if cross[n] < (lo + hi) / 2:
                            cross[n] = lo - lo_pad - NODE_GAP / 2 - half
                        else:
                            cross[n] = hi + pad + NODE_GAP / 2 + half
                        moved = True
        for ly in layers:
            ly.sort(key=lambda n: cross[n])
            for j in range(1, len(ly)):
                need = cross[ly[j - 1]] + sep(ly[j - 1], ly[j])
                if cross[ly[j]] < need:
                    cross[ly[j]] = need
        if not moved:
            break
    # main axis
    base_gap = LAYER_GAP_LR if horiz else LAYER_GAP_TD
    labeled = {}
    for chain, k, _r in chains:
        text = edges[k]["label"]
        if text:
            for a in chain[:-1]:
                labeled.setdefault(layer[a], []).append(text)
    gaps_ = []
    for li in range(nlayers - 1):
        texts = labeled.get(li, [])
        if horiz:
            gaps_.append(max([base_gap] + [max(tw(l) for l in label_lines(t, 20)) + 70 for t in texts]))
        else:
            gaps_.append(base_gap + 22 * max(0, len(texts) - 1))
    ext = [max([msize(n) for n in ly] + [0]) for ly in layers]
    mcenter, cursor = [], 0.0
    for li in range(nlayers):
        mcenter.append(cursor + ext[li] / 2)
        cursor += ext[li] + (gaps_[li] if li < len(gaps_) else 0)
    mpos = {n: mcenter[layer[n]] for ly in layers for n in ly}
    # ports
    out_by, in_by = {}, {}
    for chain, k, r in chains:
        out_by.setdefault(chain[0], []).append((cross[chain[1]], k))
        in_by.setdefault(chain[-1], []).append((cross[chain[-2]], k))
    out_off, in_off = {}, {}
    for table, store in ((out_by, out_off), (in_by, in_off)):
        for n, lst in table.items():
            lst.sort()
            cnt = len(lst)
            step = 0 if nodes[n].shape == "diamond" else min(28, csize(n) * 0.6 / max(1, cnt - 1))
            for idx, (_c, k) in enumerate(lst):
                store[(n, k)] = (idx - (cnt - 1) / 2) * step
    # per-gap jump allocation
    segs = {}
    for ci, (chain, k, r) in enumerate(chains):
        cs = [cross[chain[0]] + out_off.get((chain[0], k), 0)] + [cross[x] for x in chain[1:-1]] + \
             [cross[chain[-1]] + in_off.get((chain[-1], k), 0)]
        for j in range(len(chain) - 1):
            if abs(cs[j + 1] - cs[j]) > 0.5:
                segs.setdefault(layer[chain[j]], []).append((ci, j, cs[j], cs[j + 1]))
    jump = {}
    for li, lst in segs.items():
        left = sorted([s for s in lst if s[3] < s[2]], key=lambda s: s[2])
        right = sorted([s for s in lst if s[3] >= s[2]], key=lambda s: -s[2])
        ordered = left + right
        top = mcenter[li] + ext[li] / 2
        bot = mcenter[li + 1] - ext[li + 1] / 2
        cnt = len(ordered)
        for idx, s in enumerate(ordered):
            frac = 0.5 if cnt == 1 else 0.22 + 0.56 * idx / (cnt - 1)
            jump[(s[0], s[1])] = top + (bot - top) * frac
    paths = []
    for ci, (chain, k, r) in enumerate(chains):
        cs = [cross[chain[0]] + out_off.get((chain[0], k), 0)] + [cross[x] for x in chain[1:-1]] + \
             [cross[chain[-1]] + in_off.get((chain[-1], k), 0)]
        m_start = mpos[chain[0]] + msize(chain[0]) / 2
        m_end = mpos[chain[-1]] - msize(chain[-1]) / 2
        pts = [(m_start, cs[0])]
        for j in range(len(chain) - 1):
            if abs(cs[j + 1] - cs[j]) > 0.5:
                jm = jump[(ci, j)]
                pts.append((jm, cs[j]))
                pts.append((jm, cs[j + 1]))
        pts.append((m_end, cs[-1]))
        clean = [pts[0]]
        for p in pts[1:]:
            if abs(p[0] - clean[-1][0]) > 0.01 or abs(p[1] - clean[-1][1]) > 0.01:
                clean.append(p)
        xy = [(c, m) for m, c in clean] if not horiz else [(m, c) for m, c in clean]
        paths.append({"k": k, "pts": xy, "rev": r})
    # node rects in xy
    rects = {}
    for n in ids:
        nd = nodes[n]
        cx, cy = (mpos[n], cross[n]) if horiz else (cross[n], mpos[n])
        rects[n] = (cx - nd.w / 2, cy - nd.h / 2, nd.w, nd.h)
    return rects, paths, layer


def group_rects(nodes, groups, rects):
    out = {}
    children = {g: [] for g in groups}
    for g, info in groups.items():
        if info["parent"]:
            children[info["parent"]].append(g)
    members = {g: [] for g in groups}
    for n in nodes.values():
        for g in n.group:
            members[g].append(n.id)

    def depth_below(g):
        return 1 + max([depth_below(c) for c in children[g]], default=0)

    def calc(g):
        boxes = [rects[m] for m in members[g]]
        for c in children[g]:
            calc(c)
            boxes.append(out[c])
        if not boxes:
            return
        pad = 12 + 10 * (depth_below(g) - 1)
        x0 = min(b[0] for b in boxes) - pad
        y0 = min(b[1] for b in boxes) - pad - 20
        x1 = max(b[0] + b[2] for b in boxes) + pad
        y1 = max(b[1] + b[3] for b in boxes) + pad
        out[g] = (x0, y0, x1 - x0, y1 - y0)

    for g, info in groups.items():
        if info["parent"] is None:
            calc(g)
    return out, members


def render_flowchart(src, fig_label=""):
    direction, nodes, edges, groups = parse_flowchart(src)
    rects, paths, _layer = layout_flowchart(direction, nodes, edges, groups)
    grects, members = group_rects(nodes, groups, rects)
    # warnings for overlaps
    for g, gr in grects.items():
        for n, r in rects.items():
            if n not in members[g] and rects_overlap(gr, r) and g not in [x for x in nodes[n].group]:
                warn("subgraph '%s' cannot be drawn without covering node '%s' in %s; keep a subgraph's nodes in neighboring layers or split it into smaller subgraphs" % (groups[g]["title"], nodes[n].label, fig_label or "diagram"))
    # labels
    node_boxes = list(rects.values())
    placed, labels = [], []
    title_boxes = [(gr[0], gr[1], min(gr[2], tw(groups[g]["title"]) + 28), 22) for g, gr in grects.items()]
    def hits_other(box, own):
        for q in paths:
            if q is own:
                continue
            for a, b in zip(q["pts"], q["pts"][1:]):
                sx0, sx1 = sorted((a[0], b[0]))
                sy0, sy1 = sorted((a[1], b[1]))
                if not (sx1 < box[0] - 1 or sx0 > box[0] + box[2] + 1 or sy1 < box[1] - 1 or sy0 > box[1] + box[3] + 1):
                    return True
        return False

    for p in paths:
        e = edges[p["k"]]
        if not e["label"]:
            continue
        text = e["label"]
        llines = label_lines(text, 20)
        lw, lh = max(tw(l) for l in llines) * 0.93 + 10, 14 * len(llines) + 4
        segs = []
        for a, b in zip(p["pts"], p["pts"][1:]):
            segs.append((math.hypot(b[0] - a[0], b[1] - a[1]), a, b))
        segs.sort(key=lambda s: -s[0])
        chosen = None
        for ln, a, b in segs:
            vertical = abs(a[0] - b[0]) < 0.5
            for t in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.1, 0.9):
                cx, cy = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
                cands = [(cx - lw / 2, cy - lh / 2, lw, lh)]
                # beside the line, so a narrow gap between parallel edges is not needed
                if vertical:
                    cands += [(cx + 6, cy - lh / 2, lw, lh), (cx - 6 - lw, cy - lh / 2, lw, lh)]
                else:
                    cands += [(cx - lw / 2, cy - 4 - lh, lw, lh), (cx - lw / 2, cy + 4, lw, lh)]
                for box in cands:
                    if any(rects_overlap(box, nb, 2) for nb in node_boxes + placed + title_boxes):
                        continue
                    if hits_other(box, p):
                        continue
                    chosen = box
                    break
                if chosen:
                    break
            if chosen:
                break
        if not chosen:
            a, b = segs[0][1], segs[0][2]
            chosen = ((a[0] + b[0]) / 2 - lw / 2, (a[1] + b[1]) / 2 - lh / 2, lw, lh)
            warn("edge label '%s' overlaps another element in %s" % (text, fig_label or "diagram"))
        placed.append(chosen)
        labels.append((text, chosen, llines))
    # bounds
    allb = list(rects.values()) + list(grects.values()) + [b for _t, b, _l in labels]
    for p in paths:
        allb += [(x, y, 0, 0) for x, y in p["pts"]]
    minx = min(b[0] for b in allb) - 16
    miny = min(b[1] for b in allb) - 16
    maxx = max(b[0] + b[2] for b in allb) + 16
    maxy = max(b[1] + b[3] for b in allb) + 16
    W, H = maxx - minx, maxy - miny
    mid = next_marker()
    s = ['<svg class="diagram" viewBox="0 0 %.0f %.0f" width="%.0f" height="%.0f" role="img" aria-label="%s">' %
         (W, H, W, H, esc(fig_label or "Diagram")),
         '<defs><marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
         '<path d="M1 1 L9 5 L1 9 z" class="dg-head"/></marker></defs>' % mid]
    tx, ty = -minx, -miny
    s.append('<g transform="translate(%.1f %.1f)">' % (tx, ty))
    for g in sorted(grects, key=lambda g: -grects[g][2] * grects[g][3]):
        x, y, w, h = grects[g]
        s.append('<rect class="dg-group" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8"/>' % (x, y, w, h))
        s.append('<text class="dg-group-title" x="%.1f" y="%.1f">%s</text>' % (x + 12, y + 15, esc(groups[g]["title"])))
    for p in paths:
        e = edges[p["k"]]
        d = "M" + " L".join("%.1f %.1f" % pt for pt in p["pts"])
        cls = "dg-edge" + (" dg-dashed" if e["style"] == "dashed" else " dg-thick" if e["style"] == "thick" else "")
        mk = ('marker-start="url(#%s)"' if p["rev"] else 'marker-end="url(#%s)"') % mid
        s.append('<path class="%s" d="%s" fill="none" %s/>' % (cls, d, mk))
    for nid, nd in nodes.items():
        x, y, w, h = rects[nid]
        cx, cy = x + w / 2, y + h / 2
        if nd.shape == "diamond":
            s.append('<polygon class="dg-node dg-diamond" points="%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f"/>' %
                     (cx, y, x + w, cy, cx, y + h, x, cy))
        elif nd.shape == "store":
            ry = 7
            s.append('<path class="dg-node dg-store" d="M%.1f %.1f a%.1f %d 0 0 1 %.1f 0 v%.1f a%.1f %d 0 0 1 -%.1f 0 z"/>' %
                     (x, y + ry, w / 2, ry, w, h - 2 * ry, w / 2, ry, w))
            s.append('<path class="dg-store-line" d="M%.1f %.1f a%.1f %d 0 0 0 %.1f 0" fill="none"/>' % (x, y + ry, w / 2, ry, w))
        else:
            s.append('<rect class="dg-node" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%d"/>' %
                     (x, y, w, h, 18 if nd.shape == "round" else 6))
        s.append(multi_text(nd.lines, cx, cy + (4 if nd.shape == "store" else 0)))
    for text, (x, y, w, h), llines in labels:
        s.append('<rect class="dg-label-bg" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3"/>' % (x, y, w, h))
        s.append(multi_text(llines, x + w / 2, y + h / 2, "dg-label", 14))
    s.append("</g></svg>")
    info = {"nodes": {k: (v[0] + tx, v[1] + ty, v[2], v[3]) for k, v in rects.items()},
            "labels": [(t, (b[0] + tx, b[1] + ty, b[2], b[3])) for t, b, _l in labels],
            "groups": {g: (v[0] + tx, v[1] + ty, v[2], v[3]) for g, v in grects.items()}, "members": members}
    return "".join(s), info


# ---- sequence diagrams

SEQ_UNSUPPORTED = ("alt", "else", "opt", "loop", "par", "and", "rect", "critical", "break", "box", "activate",
                   "deactivate", "end", "link", "links", "title")


def render_sequence(src, fig_label=""):
    parts, alias, msgs, autonum = [], {}, [], False

    def add_part(pid):
        if pid not in parts:
            parts.append(pid)
            alias.setdefault(pid, pid)

    for raw in src[1:]:
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if line == "autonumber":
            autonum = True
            continue
        m = re.match(r"^(participant|actor)\s+(\w+)(?:\s+as\s+(.+))?$", line)
        if m:
            add_part(m.group(2))
            if m.group(3):
                alias[m.group(2)] = m.group(3).strip()
            continue
        m = re.match(r"^Note\s+(over|left of|right of)\s+([\w, ]+?)\s*:\s*(.*)$", line)
        if m:
            ids = [x.strip() for x in m.group(2).split(",")]
            for x in ids:
                add_part(x)
            msgs.append(("note", m.group(1), ids, m.group(3)))
            continue
        m = re.match(r"^(\w+)\s*(-->>|->>|-->|->)\s*(\w+)\s*:\s*(.*)$", line)
        if m:
            add_part(m.group(1))
            add_part(m.group(3))
            msgs.append(("msg", m.group(1), m.group(3), m.group(2), m.group(4)))
            continue
        word = line.split()[0]
        if word in SEQ_UNSUPPORTED:
            raise BuildError("sequence diagram keyword '%s' is not supported (use messages and notes only)" % word)
        raise BuildError("cannot read sequence line: " + line)
    if not parts:
        raise BuildError("sequence diagram has no participants")
    idx = {p: i for i, p in enumerate(parts)}
    bw = [max(86, tw(alias[p]) + 28) for p in parts]
    gaps = [(bw[i] + bw[i + 1]) / 2 + 24 for i in range(len(parts) - 1)]
    n = 0
    texts = []
    for m in msgs:
        if m[0] == "msg":
            n += 1
            t = ("%d. " % n if autonum else "") + m[4]
            texts.append(t)
        else:
            texts.append(m[3])
    def span_of(m):
        return abs(idx[m[1]] - idx[m[2]]) if m[0] == "msg" else 0

    for m, t in sorted(zip(msgs, texts), key=lambda z: span_of(z[0])):
        if m[0] == "msg":
            a, b = sorted((idx[m[1]], idx[m[2]]))
            need = tw(t) * 0.93 + 36
        else:
            ids = [idx[x] for x in m[2]]
            a, b = min(ids), max(ids)
            need = tw(t) * 0.93 + 24 if m[1] == "over" else 0
        cur = sum(gaps[a:b])
        if b > a and cur < need:
            add = (need - cur) / (b - a)
            for i in range(a, b):
                gaps[i] += add
    xs = [bw[0] / 2]
    for g in gaps:
        xs.append(xs[-1] + g)
    top = 44
    y = top + 34
    rows = []
    for m, t in zip(msgs, texts):
        if m[0] == "msg":
            if m[1] == m[2]:
                rows.append((m, t, y + 22, 52))
                y += 52
            else:
                rows.append((m, t, y + 26, 44))
                y += 44
        else:
            lines = label_lines(t, 34)
            h = 16 * len(lines) + 14
            rows.append((m, t, y + 8, h + 14))
            y += h + 14
    bottom = y + 16
    mid = next_marker()
    shapes, minx, maxx = [], 0.0, xs[-1] + bw[-1] / 2
    for p in parts:
        shapes.append('<line class="dg-life" x1="%.1f" y1="%d" x2="%.1f" y2="%.1f"/>' % (xs[idx[p]], top, xs[idx[p]], bottom))
    for m, t, ry, rh in rows:
        if m[0] == "msg":
            x1, x2 = xs[idx[m[1]]], xs[idx[m[2]]]
            dashed = m[3].startswith("--")
            head = m[3].endswith(">>")
            cls = "dg-edge" + (" dg-dashed" if dashed else "")
            mk = (' marker-end="url(#%s)"' % mid) if head else ""
            if x1 == x2:
                shapes.append('<path class="%s" d="M%.1f %.1f h34 v22 h-34" fill="none"%s/>' % (cls, x1, ry - 10, mk))
                shapes.append('<text class="dg-label dg-left" x="%.1f" y="%.1f" dominant-baseline="central">%s</text>' % (x1 + 42, ry, esc(t)))
                maxx = max(maxx, x1 + 42 + tw(t) * 0.93)
            else:
                d = 6 if x2 > x1 else -6
                shapes.append('<path class="%s" d="M%.1f %.1f L%.1f %.1f" fill="none"%s/>' % (cls, x1 + (0 if x2 > x1 else 0), ry, x2, ry, mk))
                lw_ = tw(t) * 0.93 + 8
                shapes.append('<rect class="dg-label-bg" x="%.1f" y="%.1f" width="%.1f" height="16" rx="3"/>' % ((x1 + x2) / 2 - lw_ / 2, ry - 19, lw_))
                shapes.append('<text class="dg-label" x="%.1f" y="%.1f" text-anchor="middle">%s</text>' % ((x1 + x2) / 2, ry - 8, esc(t)))
        else:
            ids = [idx[x] for x in m[2]]
            lines = label_lines(t, 34)
            w = max(tw(l) for l in lines) * 0.93 + 24
            h = 16 * len(lines) + 14
            if m[1] == "over":
                cx = (xs[min(ids)] + xs[max(ids)]) / 2
                w = max(w, xs[max(ids)] - xs[min(ids)] + 24)
            elif m[1] == "right of":
                cx = xs[ids[0]] + 12 + w / 2
            else:
                cx = xs[ids[0]] - 12 - w / 2
            minx = min(minx, cx - w / 2)
            maxx = max(maxx, cx + w / 2)
            shapes.append('<rect class="dg-note" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4"/>' % (cx - w / 2, ry - 6, w, h))
            shapes.append(multi_text(lines, cx, ry - 6 + h / 2, "dg-text"))
    heads = []
    for p in parts:
        w = bw[idx[p]]
        heads.append('<rect class="dg-node" x="%.1f" y="8" width="%.1f" height="32" rx="6"/>' % (xs[idx[p]] - w / 2, w))
        heads.append('<text class="dg-text" x="%.1f" y="24" text-anchor="middle" dominant-baseline="central">%s</text>' % (xs[idx[p]], esc(alias[p])))
        minx = min(minx, xs[idx[p]] - w / 2)
    ox = 16 - minx
    W, H = maxx - minx + 32, bottom + 16
    svg = ['<svg class="diagram" viewBox="0 0 %.0f %.0f" width="%.0f" height="%.0f" role="img" aria-label="%s">' %
           (W, H, W, H, esc(fig_label or "Sequence diagram")),
           '<defs><marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
           '<path d="M1 1 L9 5 L1 9 z" class="dg-head"/></marker></defs>' % mid,
           '<g transform="translate(%.1f 0)">' % ox] + shapes + heads + ["</g></svg>"]
    return "".join(svg), {"participants": parts}


def render_mermaid(code, fig_label="", line=None):
    src = [l for l in code.split("\n") if l.strip() and not l.strip().startswith("%%")]
    if not src:
        raise BuildError("empty mermaid block", line)
    head = src[0].strip()
    try:
        if head.startswith("sequenceDiagram"):
            return render_sequence(src, fig_label)[0]
        if re.match(r"^(flowchart|graph)\b", head):
            return render_flowchart(src, fig_label)[0]
    except BuildError as e:
        raise BuildError("diagram: %s" % e, line)
    raise BuildError("unsupported diagram type '%s'; use flowchart or sequenceDiagram" % head.split()[0], line)


# ---------------------------------------------------------------- HTML output

CSS = r"""
:root{--bg:#fff;--text:#1b1f24;--muted:#57606a;--border:#d0d7de;--accent:#0b5cad;--code-bg:#f6f8fa;--panel:#f6f8fa;
--dg-node:#eef3fa;--dg-stroke:#5b6f8a;--dg-edge:#3d4a5c;--dg-group:#6b7a8d;--dg-store:#eaf5ee;--dg-store-s:#4d7c5f;--dg-note:#fff8e1;
--c-k:#8250df;--c-s:#0a3069;--c-c:#57606a;--c-n:#0550ae;--c-t:#953800;--c-a:#116329;--c-v:#953800;--c-p:#0550ae;
--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e}
:root[data-theme=dark]{--bg:#0d1117;--text:#e6edf3;--muted:#9aa4af;--border:#30363d;--accent:#58a6ff;--code-bg:#161b22;--panel:#161b22;
--dg-node:#1c2a3d;--dg-stroke:#6f8fb5;--dg-edge:#9fb0c5;--dg-group:#7d8da1;--dg-store:#1b3326;--dg-store-s:#6aa882;--dg-note:#332b12;
--c-k:#d2a8ff;--c-s:#a5d6ff;--c-c:#8b949e;--c-n:#79c0ff;--c-t:#ffa657;--c-a:#7ee787;--c-v:#ffa657;--c-p:#79c0ff;
--ok:#3fb950;--warn:#d29922;--bad:#ff7b72}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0d1117;--text:#e6edf3;--muted:#9aa4af;--border:#30363d;--accent:#58a6ff;--code-bg:#161b22;--panel:#161b22;
--dg-node:#1c2a3d;--dg-stroke:#6f8fb5;--dg-edge:#9fb0c5;--dg-group:#7d8da1;--dg-store:#1b3326;--dg-store-s:#6aa882;--dg-note:#332b12;
--c-k:#d2a8ff;--c-s:#a5d6ff;--c-c:#8b949e;--c-n:#79c0ff;--c-t:#ffa657;--c-a:#7ee787;--c-v:#ffa657;--c-p:#79c0ff;
--ok:#3fb950;--warn:#d29922;--bad:#ff7b72}}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:16px}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.skip{position:absolute;left:-999px;top:0;background:var(--bg);color:var(--text);padding:8px 12px;z-index:10}
.skip:focus{left:8px;top:8px}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:40px;max-width:1180px;margin:0 auto;padding:0 24px}
header.doc{grid-column:1/-1;padding:40px 0 16px;border-bottom:1px solid var(--border)}
header.doc h1{margin:0 0 12px;font-size:2rem;line-height:1.25}
.meta{display:flex;flex-wrap:wrap;gap:8px 20px;margin:0;padding:0;font-size:.875rem;color:var(--muted)}
.meta div{display:flex;gap:6px}.meta dt{font-weight:600}.meta dd{margin:0}
.badge{display:inline-block;padding:1px 10px;border:1px solid currentColor;border-radius:999px;font-weight:600;font-size:.8rem}
.badge-approved{color:var(--ok)}.badge-draft,.badge-proposed{color:var(--warn)}.badge-superseded{color:var(--bad)}
nav.toc{position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;padding:24px 0;font-size:.875rem}
nav.toc summary{font-weight:600;cursor:pointer;margin-bottom:8px}
nav.toc ol{list-style:none;margin:0;padding:0}nav.toc li{margin:2px 0}
nav.toc a{display:block;padding:3px 10px;border-left:2px solid transparent;color:var(--muted);text-decoration:none}
nav.toc a:hover{color:var(--text)}
nav.toc a.active{color:var(--accent);border-left-color:var(--accent);font-weight:600}
nav.toc li.sub a{padding-left:24px}
.tools{margin-top:16px}
button{font:inherit;font-size:.8rem;background:var(--panel);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:3px 10px;cursor:pointer}
main{min-width:0;padding:24px 0 80px;max-width:820px}
h2,h3,h4{line-height:1.3;margin:2em 0 .6em}h2{font-size:1.5rem;border-bottom:1px solid var(--border);padding-bottom:.3em}h3{font-size:1.2rem}h4{font-size:1rem}
h2 .anchor,h3 .anchor{margin-left:.4em;font-size:.8em;text-decoration:none;visibility:hidden}
h2:hover .anchor,h3:hover .anchor,.anchor:focus{visibility:visible}
p,ul,ol{margin:.8em 0}li{margin:.25em 0}
code{font:.875em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code-bg);padding:.1em .35em;border-radius:4px}
.code{margin:1em 0;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--code-bg)}
.code-bar{display:flex;justify-content:space-between;align-items:center;padding:4px 10px;border-bottom:1px solid var(--border);font-size:.75rem;color:var(--muted)}
pre{margin:0;padding:12px 14px;overflow:auto;font:.84rem/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;tab-size:4}
pre code{background:none;padding:0;font:inherit}
.t-k{color:var(--c-k)}.t-s{color:var(--c-s)}.t-c{color:var(--c-c);font-style:italic}.t-n{color:var(--c-n)}.t-t{color:var(--c-t)}.t-a{color:var(--c-a)}.t-v{color:var(--c-v)}.t-p{color:var(--c-p)}
.d-add{color:var(--ok);background:color-mix(in srgb,var(--ok) 12%,transparent);display:inline-block;width:100%}
.d-del{color:var(--bad);background:color-mix(in srgb,var(--bad) 12%,transparent);display:inline-block;width:100%}
.d-hunk{color:var(--c-n)}
.table-wrap{overflow-x:auto;margin:1em 0}
table{border-collapse:collapse;width:100%;font-size:.9rem}
caption{caption-side:top;text-align:left;font-weight:600;font-size:.85rem;color:var(--muted);padding-bottom:6px}
th,td{border:1px solid var(--border);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--panel)}td.right,th.right{text-align:right}td.center,th.center{text-align:center}
blockquote{margin:1em 0;padding:.2em 1em;border-left:3px solid var(--border);color:var(--muted)}
.callout{margin:1em 0;padding:.6em 1em;border:1px solid var(--border);border-left-width:4px;border-radius:6px;background:var(--panel)}
.callout p{margin:.4em 0}.callout-label{font-weight:700}
.callout-warning{border-left-color:var(--warn)}.callout-note{border-left-color:var(--accent)}.callout-decision{border-left-color:var(--ok)}.callout-assumption{border-left-color:var(--muted)}
.tag{font-size:.78em;padding:0 .45em;border:1px solid currentColor;border-radius:4px;white-space:nowrap}
.tag-verified{color:var(--ok)}.tag-documented{color:var(--accent)}.tag-assumption{color:var(--warn)}
figure{margin:1.4em 0;padding:0}
.fig-body{overflow-x:auto;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg)}
figcaption{font-size:.85rem;color:var(--muted);margin-top:6px}
svg.diagram{display:block;margin:0 auto;max-width:100%;height:auto;font:13px system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.dg-node{fill:var(--dg-node);stroke:var(--dg-stroke);stroke-width:1.2}
.dg-store{fill:var(--dg-store);stroke:var(--dg-store-s);stroke-width:1.2}.dg-store-line{stroke:var(--dg-store-s);stroke-width:1.2}
.dg-group{fill:none;stroke:var(--dg-group);stroke-width:1.2;stroke-dasharray:6 4}
.dg-group-title{fill:var(--muted);font-size:12px;font-weight:600}
.dg-edge{stroke:var(--dg-edge);stroke-width:1.4}.dg-dashed{stroke-dasharray:5 4}.dg-thick{stroke-width:2.6}
.dg-head{fill:var(--dg-edge)}
.dg-text{fill:var(--text);font-size:13px}
.dg-label{fill:var(--text);font-size:12px;paint-order:stroke;stroke:var(--bg);stroke-width:5px;stroke-linejoin:round}
.dg-left{text-anchor:start}
.dg-label-bg{fill:var(--bg)}
.dg-life{stroke:var(--dg-group);stroke-width:1;stroke-dasharray:3 4}
.dg-note{fill:var(--dg-note);stroke:var(--dg-stroke);stroke-width:1}
hr{border:0;border-top:1px solid var(--border);margin:2em 0}
@media (max-width:900px){.layout{display:block;padding:0 16px}nav.toc{position:static;max-height:none;padding:12px 0}main{padding-top:8px}header.doc{padding-top:24px}h1{font-size:1.6rem}}
@media print{
html{scroll-behavior:auto}body{background:#fff;color:#000;font-size:11pt}
.layout{display:block;max-width:none;padding:0}nav.toc,.tools,.skip,.copy,.anchor{display:none!important}
main{max-width:none;padding:0}
a{color:#000}a[href^=http]::after{content:" (" attr(href) ")";font-size:.85em;word-break:break-all}
figure,table,tr,.code,.callout,blockquote,pre{break-inside:avoid}
h2,h3,h4{break-after:avoid}
.fig-body{overflow:visible;border-color:#999}.code{background:#fff;border-color:#999}pre{white-space:pre-wrap;overflow:visible}
svg.diagram{max-width:100%}
}
"""

JS = r"""
(function(){
var root=document.documentElement;
function stored(){try{return localStorage.getItem('theme')}catch(e){return null}}
function store(v){try{localStorage.setItem('theme',v)}catch(e){}}
var s=stored();if(s){root.setAttribute('data-theme',s)}
var tb=document.getElementById('theme');
function current(){var t=root.getAttribute('data-theme');if(t)return t;return matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}
function label(){if(tb)tb.textContent=current()==='dark'?'Light theme':'Dark theme'}
if(tb){tb.addEventListener('click',function(){var n=current()==='dark'?'light':'dark';root.setAttribute('data-theme',n);store(n);label()});label()}
var d=document.querySelector('nav.toc details');
if(d){if(matchMedia('(min-width:901px)').matches){d.setAttribute('open','')}else{d.removeAttribute('open')}}
document.querySelectorAll('.copy').forEach(function(b){b.addEventListener('click',function(){
var code=b.closest('.code').querySelector('code');var txt=code.textContent;
function done(){var o=b.textContent;b.textContent='Copied';setTimeout(function(){b.textContent=o},1500)}
if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(done,function(){fallback(txt,done)})}else{fallback(txt,done)}})});
function fallback(txt,done){var ta=document.createElement('textarea');ta.value=txt;ta.setAttribute('readonly','');ta.style.position='absolute';ta.style.left='-9999px';document.body.appendChild(ta);ta.select();try{document.execCommand('copy');done()}catch(e){}document.body.removeChild(ta)}
var links=Array.prototype.slice.call(document.querySelectorAll('nav.toc a'));
var heads=Array.prototype.slice.call(document.querySelectorAll('main h2[id],main h3[id]'));
var map={};links.forEach(function(a){map[a.getAttribute('href').slice(1)]=a});
var ticking=false;
function spy(){ticking=false;var cur=heads.length?heads[0]:null;
for(var i=0;i<heads.length;i++){if(heads[i].getBoundingClientRect().top<=140){cur=heads[i]}else{break}}
links.forEach(function(a){a.classList.remove('active')});
if(cur&&map[cur.id]){map[cur.id].classList.add('active')}}
window.addEventListener('scroll',function(){if(!ticking){ticking=true;requestAnimationFrame(spy)}},{passive:true});
spy();
})();
"""


def render_list(items):
    out, stack = [], []
    for indent, ordered, text in items:
        tag = "ol" if ordered else "ul"
        while stack and indent < stack[-1][0]:
            out.append("</li></%s>" % stack.pop()[1])
        if not stack or indent > stack[-1][0]:
            stack.append((indent, tag))
            out.append("<%s>" % tag)
        else:
            out.append("</li>")
        out.append("<li>%s" % inline(text))
    while stack:
        out.append("</li></%s>" % stack.pop()[1])
    return "".join(out)


def render_quote(b):
    paras, cur = [], []
    for l in b["lines"]:
        if l.strip():
            cur.append(l.strip())
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    if not paras:
        raise BuildError("empty blockquote", b["line"])
    m = re.match(r"^\*\*(Note|Warning|Decision|Assumption):\*\*\s*(.*)$", paras[0])
    if m:
        kind = m.group(1).lower()
        body = ['<p><span class="callout-label">%s:</span> %s</p>' % (m.group(1), inline(m.group(2)))]
        body += ["<p>%s</p>" % inline(p) for p in paras[1:]]
        return '<div class="callout callout-%s" role="note">%s</div>' % (kind, "".join(body))
    return "<blockquote>%s</blockquote>" % "".join("<p>%s</p>" % inline(p) for p in paras)


def build_html(text, src_name):
    _MARK[0] = 0
    meta, body, offset = split_front_matter(text)
    blocks = attach_captions(parse_blocks(body.split("\n"), offset))
    used, toc, parts = set(), [], []
    title = meta.get("title", "")
    h1_seen = False
    for b in blocks:
        t = b["t"]
        CURRENT[0] = b["line"]
        try:
            if t == "heading":
                lvl = b["level"]
                if lvl == 1 and not h1_seen:
                    h1_seen = True
                    title = plain(b["text"])
                    continue
                lvl = max(lvl, 2)
                sid = slugify(b["text"], used)
                if lvl <= 3:
                    toc.append((lvl, sid, plain(b["text"])))
                anchor = ' <a class="anchor" href="#%s" aria-label="Link to this section">#</a>' % sid if lvl <= 3 else ""
                parts.append('<h%d id="%s">%s%s</h%d>' % (lvl, sid, inline(b["text"]), anchor, lvl))
            elif t == "para":
                parts.append("<p>%s</p>" % inline(b["text"]))
            elif t == "list":
                parts.append(render_list(b["items"]))
            elif t == "hr":
                parts.append("<hr>")
            elif t == "quote":
                parts.append(render_quote(b))
            elif t == "table":
                cap = ""
                if "caption" in b:
                    k, num, ctext = b["caption"]
                    cap = "<caption>%s %d. %s</caption>" % (k, num, inline(ctext))
                head = "".join('<th scope="col"%s>%s</th>' % (' class="%s"' % a if a else "", inline(c)) for c, a in zip(b["head"], b["aligns"]))
                rows = "".join("<tr>%s</tr>" % "".join('<td%s>%s</td>' % (' class="%s"' % a if a else "", inline(c)) for c, a in zip(r, b["aligns"])) for r in b["rows"])
                parts.append('<div class="table-wrap"><table>%s<thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (cap, head, rows))
            elif t == "code":
                if b["lang"] == "mermaid":
                    label, cap, fid = "", "", ""
                    if "caption" in b:
                        k, num, ctext = b["caption"]
                        label = "Figure %d. %s" % (num, plain(ctext))
                        cap = "<figcaption>Figure %d. %s</figcaption>" % (num, inline(ctext))
                        fid = ' id="figure-%d"' % num
                    else:
                        warn("diagram has no 'Figure N.' caption", b["line"])
                    svg = render_mermaid(b["text"], label, b["line"])
                    parts.append('<figure%s><div class="fig-body">%s</div>%s</figure>' % (fid, svg, cap))
                else:
                    lang = b["lang"] or "text"
                    parts.append('<div class="code"><div class="code-bar"><span>%s</span><button type="button" class="copy" aria-label="Copy code">Copy</button></div>'
                                 '<pre tabindex="0"><code>%s</code></pre></div>' % (esc(lang), highlight(textwrap.dedent(b["text"]), lang)))
        except BuildError as e:
            if e.line is None:
                e.line = b["line"]
            raise
    if not title:
        raise BuildError("document has no title (add a '# Title' heading or a title in the front matter)")
    status = meta.get("status", "")
    chips = []
    for k in ("type", "status", "owner", "date", "version", "source"):
        if meta.get(k):
            v = esc(meta[k])
            if k == "status":
                v = '<span class="badge badge-%s">%s</span>' % (re.sub(r"\W+", "-", status.lower()), v)
            chips.append("<div><dt>%s</dt><dd>%s</dd></div>" % (k.capitalize(), v))
    toc_html = "".join('<li%s><a href="#%s">%s</a></li>' % (' class="sub"' if l == 3 else "", i, esc(t)) for l, i, t in toc)
    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body><a class="skip" href="#content">Skip to content</a>'
            '<div class="layout"><header class="doc"><h1>%s</h1><dl class="meta">%s</dl>'
            '<div class="tools"><button type="button" id="theme">Dark theme</button></div></header>'
            '<nav class="toc" aria-label="Contents"><details open><summary>Contents</summary><ol>%s</ol></details></nav>'
            '<main id="content">%s</main></div><script>%s</script></body></html>\n') % (
        esc(title), CSS, esc(title), "".join(chips), toc_html, "\n".join(parts), JS)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("doc")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args(argv)
    try:
        with open(args.doc, encoding="utf-8") as fh:
            text = fh.read()
        out = build_html(text, args.doc)
    except BuildError as e:
        print("%s:%s: error: %s" % (args.doc, e.line or "?", e), file=sys.stderr)
        return 2
    d = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(d, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)
    for line, msg in WARNINGS:
        print("%s:%s: warning: %s" % (args.doc, line or "?", msg), file=sys.stderr)
    if WARNINGS and args.strict:
        return 3
    print("wrote %s (%d bytes)" % (args.output, len(out.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
