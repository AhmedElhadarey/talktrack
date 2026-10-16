# New kinds

Three new visuals.

## Latency {#lat .build}

```chart bar title="p95 latency" unit="ms"
series: before, after
read: 420, 180
write: 610, 390
list: 220, 205
```

The [read path](#read) gained the most; [writes](#write) less; [listing](#list) barely moved.

## Where time goes {#where}

```chart hbar unit="%"
Database queries: 46
Template rendering: 22
JSON serialisation: 17
Network: 15
```

[Database queries](#database-queries) dominate.

## Scaling {#scale}

```chart line x="requests per second" y="latency" unit="ms"
x: 100, 200, 400, 800, 1600
p50: 12, 13, 15, 22, 48
p99: 40, 52, 90, 210, 640
```

The [p99 line](#p99) bends upward long before [the median](#p50).

## Running the tests {#term .build}

```terminal
$ uv run pytest -q
........................................ [100%]
720 passed in 51.2s
$ uv run ruff check
All checks passed!
```

First [the tests](#L1) run, then [the linter](#L4).

## Layout {#layout .build}

```tree
src/
  showntell/
    build.py   # Markdown to slides
    kinds.py   # charts, terminals, trees
    static/
      js/
        render.js
tests/
README.md
```

The [builder](#build.py) turns Markdown into slides, and [render.js](#render.js) draws them.
