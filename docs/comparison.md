# talktrack compared

Four ways to walk someone through a change, a module or a concept to someone who wasn't there. Each wins somewhere.

| Option | Example tools | Made by |
|---|---|---|
| **talktrack** | this project | coding agent writes Markdown; Kokoro voices it locally |
| **Screen recording** | Loom (hosted), OBS Studio (self-hosted, free) | a person talking over their screen |
| **Written PR description** | GitHub, GitLab, Gitea (self-hosted) | a person or an agent, in text |
| **Rendered MP4 explainer** | Remotion, Manim, ffmpeg plus a TTS service | a script or agent rendering frames and audio to a video file |

## Making one

| | talktrack | Screen recording | PR description | MP4 explainer |
|---|---|---|---|---|
| **Author effort** | one request to the agent | rehearse, record, often re-record | write and edit | write a script, render, check |
| **Time to first view** | about 1 s after the opening | after recording ends and uploads | after writing | after the full render |
| **Fixing one mistake** | revise one slide; audio for that slide only | re-record, or cut | edit text | re-render the video |
| **Code shown** | read from disk and git, real line numbers | whatever is on screen | pasted snippets or links | whatever the script draws |
| **Stays accurate when code moves** | regenerate from the same files | no | links rot, snippets drift | no |
| **Machine needed** | laptop; speech runs locally | laptop, mic | none | render time; often a GPU or paid TTS |
| **Data leaves the machine** | no, besides the agent's own model calls | yes, for hosted tools; no with OBS | to the git host | depends on the TTS and render service |

## Watching one

| | talktrack | Screen recording | PR description | MP4 explainer |
|---|---|---|---|---|
| **Pointer on the thing being said** | timed to the word | the presenter's cursor | none | if the script animates it |
| **Skim and search** | transcript, chapters, captions | hosted tools add transcripts | full text | captions if generated |
| **Seek by topic** | chapters per slide | scrub | headings | chapters if added |
| **Copy code from it** | yes, it is text | no | yes | no |
| **Plays offline** | exported folder, local server | downloaded file | text | single file |
| **Plays inline in chat, email, slides** | no; a link to a web page | hosted link previews | yes | yes |
| **Accessible** | captions, screen-reader text, reduced motion, keyboard | depends on the host | text is the most accessible | captions if added |

## What the others do better

- **Screen recording, real software:** shows the running app, the terminal, the debugger and the bug as it happens. talktrack shows slides about code, not a live session.
- **Screen recording, a human voice:** tone, hesitation and judgement carry trust that a synthetic voice does not. A teammate's voice suits a design decision or bad news.
- **Screen recording, hosted features:** Loom-style hosts add view counts, timestamped comments, reactions and sharing controls. talktrack has none; an exported folder is plain static files.
- **PR description, review flow:** sits where review happens, takes inline comments, renders everywhere, and needs no player or link. It is the record a reviewer must read anyway; a walkthrough supplements it.
- **PR description, speed for small changes:** three sentences beat a two-minute walkthrough for a rename or a version bump.
- **MP4 explainer, distribution:** one file plays in any chat, social feed, LMS or slide deck, with no server and no browser player.
- **MP4 explainer, motion design:** custom animation, transitions, branded intros and music. talktrack's visuals are fixed slide kinds: code, diffs, diagrams, formulas, tables, lists and images.
- **MP4 explainer, voices and languages:** cloud TTS offers cloned and many-language voices. talktrack speaks English with Kokoro's voices only, for now.

## When to pick talktrack

- **Reviewing an agent's change:** the agent that wrote the code walks you through it, grounded in the actual diff.
- **Onboarding to a module:** a narrated tour from real files, regenerated when the code changes.
- **Explaining a concept with project code:** formulas and diagrams next to the lines that implement them.
- **Private code:** nothing is uploaded to a speech or video service.

For a file anyone can play, write one page that plays offline with `talktrack export ID talk.html`, render an MP4 with `talktrack export ID talk.mp4` (needs FFmpeg and the `video` extra), or pair the walkthrough link with a short written summary.
