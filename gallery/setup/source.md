# Set up showntell and run its tests
> A first-day guide for new contributors

You can clone, sync and run the whole fast test suite in a few commands. We'll go through them, look at real output, and find where the tests live.
## Clone and sync {#sync}

```terminal
$ uv sync
$ uv run pytest
$ uv run showntell serve --speech fake
```

You need [uv](https://docs.astral.sh/uv/) and nothing else. [The sync command](#L1) creates the environment and installs everything.

[The test run](#L2) is the fast suite, and it needs no voice model or GPU. [The third command](#L3) starts the daemon with fake speech, so you can try the player without downloading a model.
## What the suite holds {#collect}

```terminal
$ uv run pytest --collect-only -q
tests/test_themes.py::test_project_theme_file
tests/test_themes.py::test_bad_project_theme_file_is_reported_not_fatal
tests/test_versions.py::test_manifests_in_sync
tests/test_versions.py::test_versions_agree
tests/test_video_export.py::test_mp4_export
tests/test_video_export.py::test_folder_export_writes_a_poster

782 tests collected in 0.41s
```

Collecting without running is a cheap way to see the shape of the suite. I trimmed the list to a few lines; the last line is real.

[Seven hundred eighty-two tests](#L9) were collected in under half a second. Nothing ran, so this only proves the suite imports cleanly.

The names say what is covered: themes, version agreement across manifests, and video export.

## The other checks {#checks}

```terminal
$ uv run pytest -m "mlx or onnx or slow"
$ uv run ruff check . && uv run ruff format --check .
$ node --test tests/js/*.test.js
```

CONTRIBUTING lists three more checks. [The marked tests](#L1) need real models or start processes, so run them when you touch speech or the daemon.

[Ruff](#L2) checks lint and formatting. [The Node command](#L3) tests the player's pure timing logic, which lives in JavaScript.
## The command line {#help}

```terminal
$ uv run showntell --help
usage: showntell [-h]
                 {mcp,serve,play,export,list,doctor,autostart,version} ...

Narrated slide walkthroughs for coding agents, voiced on your machine.

    mcp        run as a stdio MCP server
    serve      run the daemon in the foreground
    play       voice and play a Markdown walkthrough file
    export     write a walkthrough as a static folder or an MP4
    list       list recent walkthroughs
    doctor     check models, speech speed, port and daemon
    autostart  macOS: start the daemon at login
    version    print the version
```

Real help output, with the descriptions shortened. Three commands matter most to you. [Serve](#L7) runs the daemon you will test against. [Play](#L8) voices a Markdown file by hand. [Doctor](#L11) is what to run, and paste into a bug report, when something misbehaves.

## Where the tests live {#where .build}

```tree
tests/
  conftest.py
  test_format_blocks.py
  test_build_slides.py
  test_store.py
  test_mcp_server.py
  test_themes.py
  test_versions.py
  test_video_export.py
  js/
```

Everything is under [the tests folder](#tests), one file per area of the source.

Parser tests such as [format blocks](#test_format_blocks.py) cover `format.py`. [Build slides](#test_build_slides.py) covers `build.py`, and [store](#test_store.py) covers sessions. [The MCP server file](#test_mcp_server.py) exercises the tools.

[Versions](#test_versions.py) fails whenever the manifests disagree, which is why you run the sync script after bumping a version. The player's logic is tested separately, in [the js folder](#js).
