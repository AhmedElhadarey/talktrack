"""Reading Mermaid source without rendering it: node ids and labels for cues, edges for paths."""

from __future__ import annotations

import re

NODE_RE = re.compile(
    r"(?<![\w-])([A-Za-z_]\w*(?:-\w+)*)\s*(\(\[|\[\[|\[\(|\(\(\(|\(\(|\{\{|\[/|\[\\|\[|\(|\{|>)\s*(\"[^\"]*\"|[^\]\)\}\n]*?)\s*(\]\)|\]\]|\)\]|\)\)\)|\)\)|\}\}|/\]|\\\]|\]|\)|\})"
)
EDGE_LABEL_RE = re.compile(r"\|\s*\"?([^|\"]+?)\"?\s*\||--\s+([^->|][^->]*?)\s+-->")


def has_edge(source: str, a: str, b: str) -> bool:
    """Whether a flowchart draws an edge from node a to node b (on one line, in that order)."""
    arrow = r"(?:-->|---|-\.->|-\.-|==>|===|--[ox]|--\s[^-\n]*?-->|==\s[^=\n]*?==>|-\.\s[^.\n]*?\.->)"
    node = r"(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|>[^\]]*\])?"
    pat = re.compile(rf"(?<![\w-]){re.escape(a)}{node}\s*{arrow}\s*(?:\|[^|]*\|\s*)?{re.escape(b)}(?![\w-])")
    return any(pat.search(line) for line in source.split("\n"))


SEQ_MESSAGE_RE = re.compile(r"(\w+(?:-\w+)*)\s*(?:<<)?-{1,2}(?:>>|>|x|\))[+-]?\s*(\w+(?:-\w+)*)\s*:\s*(.+)$")


def sequence_messages(src: str) -> list[tuple[str, str, str]]:
    """(from, to, text) of each message in a sequence diagram, in source order."""
    out = []
    for raw in src.split("\n"):
        m = SEQ_MESSAGE_RE.match(raw.split("%%", 1)[0].strip())
        if m:
            out.append((m.group(1), m.group(2), m.group(3).strip()))
    return out


def parse_mermaid(src: str) -> tuple[dict, list, list]:
    """Node ids with labels (in source order), plus edge and message labels. Best effort."""
    nodes: dict[str, str] = {}
    order: list[str] = []
    labels: list[str] = []
    first = ""
    for raw in src.split("\n"):
        line = raw.split("%%", 1)[0].strip()
        if not line:
            continue
        if not first:
            first = line.split()[0].lower()
            continue
        if first in ("flowchart", "graph"):
            if line.startswith(("subgraph", "end", "classDef", "class ", "style ", "linkStyle", "click ", "direction")):
                m = re.match(r"subgraph\s+([\w-]+)\s*\[(.+?)\]", line)
                if m:
                    labels.append(m.group(2).strip('"'))
                continue
            for m in NODE_RE.finditer(line):
                nid, label = m.group(1), m.group(3).strip().strip('"')
                if nid not in nodes:
                    order.append(nid)
                nodes[nid] = label or nodes.get(nid, nid)
            stripped = NODE_RE.sub(lambda m: m.group(1), line)
            stripped = re.sub(r"\|[^|]*\|", " ", stripped)
            # edge text written inline: a -- calls --> b, a -. maybe .-> b, a == sure ==> b
            stripped = re.sub(r"(--|-\.|==)\s+[^-=.>][^>]*?\s+(-->|\.->|==>|---)", r" \2 ", stripped)
            for nid in re.findall(r"(?<![\w-])([A-Za-z_]\w*(?:-\w+)*)(?![\w])", stripped):
                if nid in ("end", "subgraph", "TD", "TB", "LR", "RL", "BT") or nid in nodes:
                    continue
                if re.search(rf"(?<![\w-]){re.escape(nid)}\s*(-|=|&|$)", stripped) or re.search(
                    rf"(>|-|\||&)\s*{re.escape(nid)}(?![\w-])", stripped
                ):
                    nodes[nid] = nid
                    order.append(nid)
            for m in EDGE_LABEL_RE.finditer(line):
                labels.append((m.group(1) or m.group(2) or "").strip())
        elif first == "sequencediagram":
            m = re.match(r"(?:participant|actor)\s+([\w-]+)(?:\s+as\s+(.+))?$", line)
            if m:
                if m.group(1) not in nodes:
                    order.append(m.group(1))
                nodes[m.group(1)] = (m.group(2) or m.group(1)).strip()
                continue
            m = SEQ_MESSAGE_RE.match(line)
            if m:
                for nid in (m.group(1), m.group(2)):
                    if nid not in nodes:
                        nodes[nid] = nid
                        order.append(nid)
                labels.append(m.group(3).strip())
        elif first.startswith("statediagram"):
            m = re.match(r'state\s+"([^"]+)"\s+as\s+([\w-]+)', line)
            if m:
                nodes[m.group(2)] = m.group(1)
                order.append(m.group(2))
                continue
            m = re.match(r"([\w\[\]*-]+)\s*-->\s*([\w\[\]*-]+)\s*(?::\s*(.+))?$", line)
            if m:
                for nid in (m.group(1), m.group(2)):
                    if nid != "[*]" and nid not in nodes:
                        nodes[nid] = nid
                        order.append(nid)
                if m.group(3):
                    labels.append(m.group(3).strip())
        else:
            for m in re.finditer(r"\"([^\"]{2,})\"|\[([^\]]{2,})\]", line):
                labels.append((m.group(1) or m.group(2)).strip())
    return nodes, labels, order
