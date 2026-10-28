# Security

## Model

talktrack is a local tool. The daemon listens on 127.0.0.1 by default and holds walkthroughs that may contain your source code.

| Threat | Protection |
|---|---|
| **A web page you visit reads your walkthroughs** (DNS rebinding) | every route checks the Host header; MCP has its own rebinding check |
| **A web page writes to talktrack** (cross-site requests) | POSTs must be JSON and same-origin; player reports need a per-walkthrough key |
| **Guessing a walkthrough or playlist link** | ids are 128-bit random values |
| **Writing to someone else's walkthrough** | writes need the write key, which only the agent receives |
| **Reading files outside the project** | paths are resolved, symlinks included, and must stay inside the project root |
| **A repository's git config running commands** | only `git rev-parse` and `git cat-file` run, with fsmonitor disabled; diffs are computed in Python |
| **An export overwriting files** | `export_walkthrough` writes only files ending in `.mp4` or `.html`, by default in the project folder; an absolute path must be inside a folder the client shares, when it shares any |
| **Text from the player steering the agent** | player reports reach the agent labelled as untrusted |
| **Serving to other devices** | binding to a non-loopback address requires an access token, added to every link and kept in a cookie; MCP and RPC stay local-only |

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability" on this repository, not in a public issue. Expect a reply within a week.
