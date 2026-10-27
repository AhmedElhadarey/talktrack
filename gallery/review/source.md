# Review: a missing browser is loud
> Commit dbb81ae in showntell

The change makes finish_walkthrough warn when Chromium is missing. It is mostly right, but one fix probably won't work for the user who follows it.
## Verdict: sound, fix one thing first {#verdict}
> Approve once the stale-daemon gap is closed

The core change is correct. A missing browser now raises a warning, `no_browser`, instead of an info line, and the tests cover it.

But the fix it tells users to follow can silently fail. That is the one should-fix. The rest is minor.
## What it gets right {#correct}
> Return value now carries a code and a reason

```diff
@@ _check_layout @@
         except CaptureUnavailable as e:
-            return str(e)
+            log_once(e)
+            return "no_browser", e.reason
         except TimeoutError:
-            return f"drawing took over {S} s"
+            return "not_checked", f"over {S} s"
```

`_check_layout` used to return one string. Now it returns [a code and a reason](#+L4 "no_browser"), so a missing browser and a failed drawing can be told apart.

[The timeout](#+L6 "not_checked") stays an info finding. That split is right: a missing install is the user's to fix, a timeout is not.

The log line fires once per daemon, so it can't flood. I also checked that `None` still means the check was off, so `SHOWNTELL_FINISH_CHECK=0` stays quiet.

## Should-fix: the fix may not take effect {#stale-daemon .side}
> :triangle-alert: The hint never mentions `showntell stop`
> Same version and build means the old daemon is reused
> The `video` extra changes dependencies, not the build id

```python
def same_build(lock: dict) -> bool:
    return (
        lock.get("version") == _version()
        and lock.get("build") == build_id()
    )
```

Suspected from reading the code, not run. [The build check](#L3 "lock.get(\"version\")") compares [the version](#L3) and [a hash of showntell's own files](#L4 "build_id()").

So a running base-install daemon [counts as current](#L1 "same_build") and is reused. It never imported Playwright, so finish still says `no_browser`.

Name `showntell stop` in the hint, or have `ensure` check whether Playwright imports.

The author may say a fresh machine has no daemon. True, but this warning appears mid-session.

## Should-fix: the hint now misleads export {#export-hint .pair}
> The shared hint says to start `mcp`; export users ran `export`

```python
# before: capture.py
INSTALL_HINT = (
    "run it with the video extra, e.g. "
    "`uvx --from 'showntell[video]' "
    "showntell export ...`"
)
```

```python
# after: capture.py
def install_hint() -> str:
    from .doctor import BROWSER_HINT
    return BROWSER_HINT  # "...showntell mcp"
```

The old hint was written for export. The new one reuses [the doctor text](# "BROWSER_HINT"), which tells you to start `showntell mcp`.

So `showntell export --mp4` without Playwright now prints a command for the MCP server. The install and Chromium steps still apply, so it's confusing rather than wrong.

Also, `capture` now imports `doctor` for a string. Move the constants somewhere neutral, like `config`, so the dependency points one way.

## Minor findings {#minor .cards}
> Each is small; none blocks the merge

- :pin: **Version pin**: `v0.1.6` is hardcoded in doctor and the README. The test guards doctor only.
- :message-square: **Commands missing**: the finding tells the agent to relay `showntell doctor`, not the commands themselves.
- :flask-conical: **Test gaps**: nothing covers the log-once flag or the `not_checked` tuple path.
- :globe: **Global flag**: `_browser_warned` is module state, so tests can leak into each other.

Four small things. [The version pin](#version-pin) is the one most likely to bite: the next release leaves stale commands in the README.

[The missing commands](#commands-missing) cost the user a step; the message could carry them.

[The test gaps](#test-gaps) and [the global flag](#global-flag) go together: add a fixture that resets the flag, then test the log-once path.

## What I'd ask the author to change {#ranked}
> Ranked by severity

| Finding | Severity | Ask |
|---|---|---|
| Stale daemon reused | should-fix | Name `showntell stop`, or detect Playwright |
| Export hint says `mcp` | should-fix | Give export its own hint; move the constants |
| Version pin in README | minor | Test it, as doctor's is |
| Commands not in finding | minor | Put them in the message |
| Test gaps and global flag | minor | Reset fixture, then log-once test |

The change does what it says, and the tests pass for what they cover. [The stale daemon](#row-1) comes first, because it decides whether the warning leads anywhere. [The export hint](#row-2) is next.

The rest are [small clean-ups](#row-3) that can follow in another commit.
