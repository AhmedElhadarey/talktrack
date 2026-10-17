# Simplifying parse_tree
> src/showntell/kinds.py

The tree parser does three jobs in one 45-line function. We'll pull them apart, one small edit at a time.
## Three jobs in one loop {#three-jobs}

```python file=src/showntell/kinds.py lines=204-250 scroll
```

First, [the scan loop](#L209-227) turns each line into a depth, a name and a note. It also guesses the indent unit.

Second, [the rebase](#L228-231) shifts depths so the shallowest entry is zero.

Third, [the second loop](#L232-243) decides which entries are folders, repairs bad indents and builds ids from the parent path.

Last, [a dedupe pass](#L244-248) appends tildes to repeated ids.
## Simplify the scan loop {#scan-loop}

```python step scroll
for raw in body.split("\n"):
    if not raw.strip():
        continue
    raw = raw.rstrip()
    m = TREE_RE.match(raw)
    lead, rest = m.group(1), m.group(2).strip()
    if re.match(r"^[\s│|]*[├└`]", raw):  # drawn by `tree`
        depth = len(re.findall(r"[│|]\s{1,3}|\s{4}", lead)) + 1
    else:
        spaces = len(raw) - len(raw.lstrip(" "))
        if spaces and unit is None:
            unit = spaces
        depth = spaces // (unit or 2)
    note = ""
    if " #" in rest:
        rest, _, note = rest.partition(" #")
        rest, note = rest.strip(), note.strip()
    if rest and rest != ".":
        rows.append([depth, rest, note])
```

```python step scroll
for raw in body.split("\n"):
    if not raw.strip():
        continue
    raw = raw.rstrip()
    m = TREE_RE.match(raw)
    lead, rest = m.group(1), m.group(2).strip()
    if re.match(r"^[\s│|]*[├└`]", raw):  # drawn by `tree`
        depth = len(re.findall(r"[│|]\s{1,3}|\s{4}", lead)) + 1
    else:
        spaces = len(raw) - len(raw.lstrip(" "))
        unit = unit or spaces
        depth = spaces // (unit or 2)
    name, _, note = rest.partition(" #")
    name, note = name.strip(), note.strip()
    if name and name != ".":
        rows.append((depth, name, note))
```

The first loop has two small knots. The note handling [tests for a hash](#L18-L20) and then partitions, but `partition` already copes with a missing separator. So [one line](#step-2) does it.

The indent unit is [set only once](#L13-L14). Writing it as an `or` expression gives the same result with half the lines. Rows also become tuples, since nothing mutates them yet.

That frees the rebase step to be absorbed elsewhere.

## Merge the three trailing passes {#merge-passes}

```python step scroll
base = min(r[0] for r in rows) if rows else 0
for r in rows:
    r[0] -= base
items, stack = [], []
for i, (depth, name, note) in enumerate(rows):
    folder = name.endswith("/") or (i + 1 < len(rows) and rows[i + 1][0] > depth)
    if depth > len(stack):
        problems.append(f"`{name}` is indented deeper than the entry above it")
        depth = len(stack)
    del stack[depth:]
    clean = name.rstrip("/")
    items.append({"id": "/".join([*stack, clean]), "name": clean + ("/" if folder else ""), "depth": depth,
                  "folder": folder, "note": note})
    if folder:
        stack.append(clean)
seen: set[str] = set()
for it in items:
    while it["id"] in seen:
        it["id"] += "~"
    seen.add(it["id"])
```

```python step scroll
base = min((d for d, _, _ in rows), default=0)
items, stack, seen = [], [], set()
for i, (depth, name, note) in enumerate(rows):
    depth -= base
    folder = name.endswith("/") or (i + 1 < len(rows) and rows[i + 1][0] - base > depth)
    if depth > len(stack):
        problems.append(f"`{name}` is indented deeper than the entry above it")
        depth = len(stack)
    del stack[depth:]
    clean = name.rstrip("/")
    id_ = "/".join([*stack, clean])
    while id_ in seen:
        id_ += "~"
    seen.add(id_)
    items.append({"id": id_, "name": clean + ("/" if folder else ""), "depth": depth, "folder": folder, "note": note})
    if folder:
        stack.append(clean)
```

The tail has three passes. [The rebase](#L1-L3) mutates every row just to subtract the minimum. [The dedupe](#L19-L22) walks every item a second time.

Both fold into the main loop. [The base](#step-2) is computed once and subtracted as we go. The dedupe becomes [a local id](#L12-L15) checked against a set before the item is built.

Behaviour is the same, with two fewer loops and no mutation of rows.
## What changed {#result}

```stat
Loops over items: 3 -> 2
Lines in function: 46 -> 38
Behaviour changes: 0
```

Two passes are gone, and the note and indent-unit logic shrank. The output is the same dicts and the same problem messages.

Next step, if you want it: move the depth rules into their own helper, so the scan loop only reads lines. Run the existing tree tests before and after, since the dedupe order and the indent-error messages are the parts most likely to slip.
