"""Measure how many technical terms the voice gets wrong, by speech-recognition round trip.

Each test sentence below is narration Markdown, as an agent would write it. The script voices
it with the real MLX backend (Kokoro), transcribes the audio with Whisper, and checks that every
technical term in the sentence came back in a recognisable spoken form. The result is the number
of mispronounced or dropped terms per minute of audio; the target is at most one.

    HF_HUB_OFFLINE=1 uv run --with mlx-whisper scripts/pronunciation_check.py
    uv run --with mlx-whisper scripts/pronunciation_check.py --pronounce old_pronounce.py   # before/after
    uv run --with mlx-whisper scripts/pronunciation_check.py -k json -v                       # some cases, verbose
    uv run --with mlx-whisper scripts/pronunciation_check.py --confirm-with mlx-community/whisper-small-mlx

Kokoro's audio varies a little from run to run, and Whisper large-v3-turbo now and then drops a
correctly spoken phrase (it wrote "big O ," for "big O of n log n" in most runs), so expect a
spread of a few terms between runs; --confirm-with separates those slips from real errors.

Needs Apple silicon, the cached Kokoro MLX model and mlx-community/whisper-large-v3-turbo.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
from num2words import num2words

from showntell.format import parse_narration
from showntell.speech.base import SAMPLE_RATE
from showntell.speech.narrator import timed_words

WHISPER = "mlx-community/whisper-large-v3-turbo"

# (narration, {term: accepted spoken forms}). A term passes when any form appears in the
# normalised transcript. Forms are written as a listener would type them; `normalise` turns
# digits, symbols and dotted names into words on both sides, so "config.py", "config dot py"
# and "Config.py" compare equal. Only forms that sound right are accepted.
CASES: list[tuple[str, dict[str, list[str]]]] = [
    # -- code spans: identifiers ---------------------------------------------------------
    (
        "Call `useEffect` after the first render, and read `HF_HUB_OFFLINE` from the environment.",
        {"useEffect": ["use effect"], "HF_HUB_OFFLINE": ["hf hub offline"]},
    ),
    (
        "The helper `parse_narration` returns a `Narration` with `display_text` set.",
        {"parse_narration": ["parse narration"], "display_text": ["display text"]},
    ),
    (
        "The `max_retries` setting and the `MAX_BATCH_SIZE` constant control the backoff.",
        {"max_retries": ["max retries"], "MAX_BATCH_SIZE": ["max batch size"]},
    ),
    ("The tokenizer splits `getUserById` into four words.", {"getUserById": ["get user by id"]}),
    (
        "Use kebab-case names like `dry-run` and `--no-cache`.",
        {"dry-run": ["dry run"], "--no-cache": ["dash dash no cache", "double dash no cache", "dash dash no cash"]},
    ),
    ("Use `os.path.join` instead of string concatenation.", {"os.path.join": ["os dot path dot join"]}),
    (
        "Square it with `x ** 2` or call `math.pow`.",
        {"x ** 2": ["x to the power of two", "x star star two", "x power two"], "math.pow": ["math dot pow"]},
    ),
    (
        "The `NaN` check uses `isinstance` and `len()`.",
        {"NaN": ["nan", "not a number"], "isinstance": ["is instance"], "len": ["len", "length"]},
    ),
    ("The `async` handler awaits a `Promise` and yields chunks.", {"async": ["async", "a sync"], "Promise": ["promise"]}),
    ("The `SELECT` query joins `users` on `user_id`.", {"user_id": ["user id"]}),
    # -- code spans: operators -------------------------------------------------------------
    (
        "The check `a == b` fails, so `a != b` holds.",
        {
            "==": ["a double equals b", "a equals equals b", "a is equal to b", "a equals b"],
            "!=": ["a not equals b", "a not equal b", "a is not equal to b", "a does not equal b", "a bang equals b", "a not equal to b"],
        },
    ),
    ("An arrow function like `x => x * 2` doubles each value.", {"=>": ["x arrow x", "x fat arrow x"], "*": ["x times two", "x star two"]}),
    ("The signature `parse(text) -> Token` returns one token.", {"->": ["arrow token", "returns token", "to token"]}),
    (
        "Combine the guards with `&&` and the fallbacks with `||`.",
        {
            "&&": ["guards with and", "guards with double ampersand", "guards with ampersand ampersand"],
            "||": ["fallbacks with or", "fallbacks with double pipe", "fallbacks with pipe pipe"],
        },
    ),
    (
        "Compare `a <= b` and `c >= d` in one pass.",
        {
            "<=": ["a less than or equal to b", "a less than or equals b", "a is less than or equal to b"],
            ">=": ["c greater than or equal to d", "c greater than or equals d", "c is greater than or equal to d"],
        },
    ),
    ("Increment the counter with `count += 1` before the loop.", {"+=": ["count plus equals one"]}),
    (
        "`std::io::Result` wraps the error.",
        {
            "::": [
                "std colon colon io colon colon result",
                "s t d colon colon io colon colon result",
                "standard colon colon io colon colon result",
            ]
        },
    ),
    # -- code spans: files, paths, flags, numbers ------------------------------------------
    (
        "Open `config.py` and `package.json` side by side.",
        {"config.py": ["config dot py", "config dot pie"], "package.json": ["package dot json", "package dot jason"]},
    ),
    (
        "The styles live in `App.tsx` and `theme.ts`.",
        {"App.tsx": ["app dot tsx", "app dot t s x"], "theme.ts": ["theme dot ts", "theme dot t s"]},
    ),
    (
        "Settings come from `pyproject.toml` and `docker-compose.yaml`.",
        {
            "pyproject.toml": ["pyproject dot toml", "pi project dot toml", "pie project dot toml", "py project dot toml"],
            "docker-compose.yaml": [
                "docker compose dot yaml",
                "docker compose dot yml",
                "docker compose dot yamel",
                "docker compose dot yammel",
            ],
        },
    ),
    (
        "Edit `src/server/routes.ts` to add the endpoint.",
        {
            "src/server/routes.ts": [
                "src slash server slash routes dot ts",
                "source slash server slash routes dot ts",
                "s r c slash server slash routes dot ts",
                "source slash server slash roots dot ts",
            ]
        },
    ),
    (
        "Write the settings to `~/.config/app/settings.json` on first run.",
        {
            "~/.config": [
                "tilde slash dot config slash app slash settings dot json",
                "home slash dot config slash app slash settings dot json",
            ]
        },
    ),
    (
        "Start the server with `--port 8080` and `--verbose`.",
        {"--port": ["dash dash port 8080", "double dash port 8080"], "--verbose": ["dash dash verbose", "double dash verbose"]},
    ),
    (
        "Delete it with `rm -rf build` and run `sudo chmod +x deploy.sh` once.",
        {
            "rm -rf": ["rm dash rf", "rm dash r f", "r m dash r f", "r m dash rf", "rm rf build"],
            "sudo": ["sudo", "sue doe", "sue do"],
            "chmod": ["chmod", "ch mod", "change mod", "c h mod"],
            "+x": ["plus x"],
            "deploy.sh": ["deploy dot sh", "deploy dot s h"],
        },
    ),
    (
        "The colour is `0x1F`, and the server binds `127.0.0.1` on port `5432`.",
        {"0x1F": ["0x1f", "hex 1f", "hex one f"], "127.0.0.1": ["127.0.0.1", "one two seven dot zero dot zero dot one"]},
    ),
    # -- code spans: acronyms and names ----------------------------------------------------
    (
        "We call the `API` over `HTTP`, and parse the `JSON` and `YAML` responses.",
        {"API": ["api"], "HTTP": ["http"], "JSON": ["json", "jason"], "YAML": ["yaml", "yamel", "yammel"]},
    ),
    ("The `CPU` and `GPU` share memory, so the `SQL` queries stay fast.", {"CPU": ["cpu"], "GPU": ["gpu"], "SQL": ["sql", "sequel"]}),
    (
        "The `TODO` in `README.md` mentions the `CHANGELOG`.",
        {"TODO": ["todo", "to do"], "README.md": ["readme dot md", "read me dot md"], "CHANGELOG": ["changelog", "change log"]},
    ),
    (
        "The `OAuth` flow returns a `JWT` signed with `RSA`.",
        {"OAuth": ["oauth", "o auth", "oh auth"], "JWT": ["jwt", "jot"], "RSA": ["rsa"]},
    ),
    ("Enable `CORS` and set a strict `CSP` header.", {"CORS": ["cors", "course", "chorus"], "CSP": ["csp"]}),
    (
        "The `i18n` keys and `a11y` labels ship with the `k8s` manifests.",
        {
            "i18n": ["i18n", "internationalization"],
            "a11y": ["a11y", "accessibility"],
            "k8s": ["k8s", "kubernetes", "k eights", "kates", "cates"],
        },
    ),
    (
        "`Next.js` builds on `React`, and `Node.js` runs it.",
        {"Next.js": ["next js", "next dot js", "nextjs"], "Node.js": ["node js", "node dot js", "nodejs"]},
    ),
    ("We wrote it in `C++` and ported the parser to `C#`.", {"C++": ["c plus plus"], "C#": ["c sharp"]}),
    (
        "Load the weights from `model.safetensors` into `MLX`.",
        {"model.safetensors": ["model dot safetensors", "model dot safe tensors"], "MLX": ["mlx"]},
    ),
    (
        "Use `zsh` with `tmux` and `vim`.",
        {"zsh": ["zsh", "z shell", "zee shell", "zed shell"], "tmux": ["tmux", "t mux", "tee mux"], "vim": ["vim"]},
    ),
    (
        "Clone it over `ssh`, then `rsync` the build and `curl` the health check.",
        {"ssh": ["ssh"], "rsync": ["rsync", "r sync", "are sync"], "curl": ["curl"]},
    ),
    (
        "Install it with `brew install ffmpeg` or `apt-get install ffmpeg`.",
        {"brew": ["brew install"], "ffmpeg": ["ffmpeg", "ff mpeg", "f f mpeg", "f f m peg"], "apt-get": ["apt get"]},
    ),
    ("Run `uv run pytest` to test it.", {"uv": ["uv run"], "pytest": ["pytest", "py test", "pie test"]}),
    ("Tokens stream over a `WebSocket`, with `gRPC` as a fallback.", {"WebSocket": ["websocket", "web socket"], "gRPC": ["grpc", "g rpc"]}),
    # -- plain narration: units, versions, complexity, links --------------------------------
    ("The request takes 5ms and the page loads in 1.5s.", {"5ms": ["5 milliseconds", "5ms"], "1.5s": ["1.5 seconds", "1.5s"]}),
    (
        "Each worker uses 200MB, and the cache holds 4KB pages.",
        {"200MB": ["200 megabytes", "200mb", "200 meg"], "4KB": ["4 kilobytes", "4kb", "4k"]},
    ),
    (
        "The animation runs at 60fps with audio at 24kHz.",
        {"60fps": ["60 fps", "60 frames per second"], "24kHz": ["24 khz", "24 kilohertz"]},
    ),
    (
        "The new path is 3x faster and uses 16 GB of memory.",
        {"3x": ["3 times faster", "3x faster"], "16 GB": ["16 gigabytes", "16gb", "16 gigs"]},
    ),
    (
        "Downloads run at 100 Mbps over a 10 ms round trip.",
        {"100 Mbps": ["100 megabits per second", "100mbps", "100 megabits a second"], "10 ms": ["10 milliseconds", "10ms"]},
    ),
    (
        "Our p99 latency dropped from 120 ms to 45 ms.",
        {"p99": ["p99", "p ninety nine"], "120 ms": ["120 milliseconds", "120ms"], "45 ms": ["45 milliseconds", "45ms"]},
    ),
    ("We upgraded to v2.3.1 last week.", {"v2.3.1": ["version 2.3.1", "v2.3.1", "v 2.3.1"]}),
    ("The service needs Python 3.13 and Node 22.", {"Python 3.13": ["python 3.13", "python three thirteen"], "Node 22": ["node 22"]}),
    (
        "Sorting is O(n log n), a lookup is O(1), and the naive scan is O(n^2).",
        {
            "O(n log n)": ["o of n log n", "o n log n", "o(n log n)", "order n log n"],
            "O(1)": ["o of 1", "o 1", "o(1)", "order 1"],
            "O(n^2)": ["o of n squared", "o n squared", "o(n squared)", "o(n^2)", "o(n²)", "order n squared"],
        },
    ),
    (
        "Docs live at https://docs.example.com/guide and the code at github.com/acme/widgets.",
        {"docs URL": ["docs dot example dot com slash guide"], "github URL": ["github dot com slash acme slash widgets"]},
    ),
    (
        "Open the README.md file and the config.yaml next to it.",
        {
            "README.md": ["readme dot md", "read me dot md"],
            "config.yaml": ["config dot yaml", "config dot yamel", "config dot yammel", "config dot yml"],
        },
    ),
    (
        "The JSON and YAML files feed the API over HTTPS.",
        {"JSON": ["json", "jason"], "YAML": ["yaml", "yamel", "yammel"], "API": ["api"], "HTTPS": ["https"]},
    ),
    ("Run it on AWS or GCP, or on any Linux box with a GPU.", {"AWS": ["aws"], "GCP": ["gcp"], "Linux": ["linux"], "GPU": ["gpu"]}),
    # -- plain narration: product names and jargon ----------------------------------------
    (
        "The cluster runs on Kubernetes, managed with kubectl and Helm.",
        {
            "Kubernetes": ["kubernetes"],
            "kubectl": [
                "kubectl",
                "kube control",
                "cube control",
                "kube ctl",
                "cube ctl",
                "kube cuttle",
                "cube cuttle",
                "kubecontrol",
                "cubecontrol",
            ],
        },
    ),
    ("Requests pass through nginx before they reach Django.", {"nginx": ["nginx", "engine x"], "Django": ["django", "jango"]}),
    (
        "State lives in Postgres, with Redis as a cache and SQLite for tests.",
        {"Postgres": ["postgres", "postgresql"], "Redis": ["redis"], "SQLite": ["sqlite", "sequel lite", "sequel light", "s q lite"]},
    ),
    (
        "The models are served by Ollama and vLLM on CUDA.",
        {"Ollama": ["ollama", "olama", "o llama", "oh llama"], "vLLM": ["vllm", "v llm"], "CUDA": ["cuda", "kuda"]},
    ),
    (
        "We validate input with Pydantic and serve it with FastAPI and uvicorn.",
        {
            "Pydantic": ["pydantic", "pi dantic", "pie dantic"],
            "FastAPI": ["fastapi", "fast api"],
            "uvicorn": ["uvicorn", "uv corn", "uvi corn"],
        },
    ),
    (
        "Tests run under pytest, with mypy and ruff checking types and style.",
        {"pytest": ["pytest", "py test", "pie test"], "mypy": ["mypy", "my pie", "my py"], "ruff": ["ruff", "rough"]},
    ),
    (
        "The frontend uses Vite, Vue and Tailwind.",
        {"frontend": ["frontend", "front end"], "Vite": ["vite", "veet", "veat"], "Vue": ["vue", "view", "vew"], "Tailwind": ["tailwind"]},
    ),
    (
        "We deploy to Vercel and Cloudflare, and keep secrets out of GitHub.",
        {"Vercel": ["vercel", "versal", "vercell"], "Cloudflare": ["cloudflare", "cloud flare"], "GitHub": ["github", "git hub"]},
    ),
    (
        "NumPy arrays and PyTorch tensors can share memory.",
        {"NumPy": ["numpy", "num py", "num pie"], "PyTorch": ["pytorch", "pie torch", "py torch"]},
    ),
    (
        "Read from stdin, write to stdout, and log to stderr.",
        {
            "stdin": ["stdin", "standard in", "standard input", "std in"],
            "stdout": ["stdout", "standard out", "standard output", "std out"],
            "stderr": ["stderr", "standard error", "standard err", "std err"],
        },
    ),
    (
        "Each struct holds a tuple, an enum and a dict.",
        {"struct": ["struct"], "tuple": ["tuple"], "enum": ["enum", "e num"], "dict": ["dict"]},
    ),
    (
        "Guard the map with a mutex, and pass extra options as kwargs.",
        {"mutex": ["mutex", "mute x"], "kwargs": ["kwargs", "kw args", "keyword args", "keyword arguments", "quargs"]},
    ),
    (
        "The webhook posts to localhost under a new namespace.",
        {"webhook": ["webhook", "web hook"], "localhost": ["localhost", "local host"], "namespace": ["namespace", "name space"]},
    ),
    (
        "We moved the codebase into a monorepo with one shared lockfile.",
        {"codebase": ["codebase", "code base"], "monorepo": ["monorepo", "mono repo"], "lockfile": ["lockfile", "lock file"]},
    ),
    (
        "Debounce the handler and memoize the tokenizer output.",
        {"Debounce": ["debounce", "de bounce"], "memoize": ["memoize", "memo ize"], "tokenizer": ["tokenizer", "tokeniser"]},
    ),
    (
        "We cache embeddings in pgvector and query them through LangChain.",
        {"embeddings": ["embeddings"], "pgvector": ["pgvector", "pg vector", "p g vector"], "LangChain": ["langchain", "lang chain"]},
    ),
    (
        "The daemon runs under systemd, and Kafka streams feed the Grafana dashboards.",
        {"daemon": ["daemon", "demon"], "systemd": ["systemd", "system d"], "Kafka": ["kafka"], "Grafana": ["grafana"]},
    ),
    (
        "Serialize the config with protobuf and send it over gRPC.",
        {
            "Serialize": ["serialize", "serialise"],
            "config": ["config"],
            "protobuf": ["protobuf", "proto buf", "protobuff"],
            "gRPC": ["grpc", "g rpc"],
        },
    ),
]


# -- comparing ------------------------------------------------------------------------------

SYMBOLS = [
    ("c++", " c plus plus "),
    ("c#", " c sharp "),
    ("===", " triple equals "),
    ("!==", " not equals "),
    ("==", " double equals "),
    ("!=", " not equals "),
    ("=>", " arrow "),
    ("->", " arrow "),
    ("<=", " less than or equal to "),
    (">=", " greater than or equal to "),
    ("&&", " and "),
    ("||", " or "),
    ("+=", " plus equals "),
    ("**", " star star "),
    ("::", " colon colon "),
    ("²", " squared "),
    ("^2", " squared "),
    ("~", " tilde "),
    ("+", " plus "),
    ("=", " equals "),
    ("&", " and "),
    ("%", " percent "),
    ("/", " slash "),
]


def _number(m: re.Match) -> str:
    s = m.group(0)
    if len(s) > 1 and s.startswith("0"):
        return " " + " ".join(num2words(int(c)) for c in s) + " "
    return " " + num2words(int(s)).replace("-", " ").replace(",", "") + " "


def normalise(text: str) -> str:
    """Lowercase words only: symbols named, dotted names split with "dot", numbers in words."""
    s = text.lower().replace("’", "'")
    for sym, word in SYMBOLS:
        s = s.replace(sym, word)
    s = re.sub(r"(?<![\w-])--(?=\w)", " dash dash ", s)
    s = re.sub(r"(?<![\w-])-(?=[a-z])", " dash ", s)
    s = re.sub(r"\.(?=\w)", " dot ", s)  # config.py, os.path, .env, 3.13
    s = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", s)  # p99, 5ms, i18n
    s = re.sub(r"\d+", _number, s)
    s = re.sub(r"[^a-z0-9']+", " ", s)
    s = re.sub(r"\bpoint\b", "dot", s)  # "three point thirteen" == "3.13"
    s = s.replace("'", "")
    return " " + " ".join(s.split()) + " "


def check(transcript: str, terms: dict[str, list[str]]) -> list[str]:
    """Terms whose accepted forms are all missing from the transcript. Whisper sometimes glues
    words together ("Zshell", "xdeploy.sh"), so longer forms may also match with spaces removed."""
    heard = normalise(transcript)
    squashed = heard.replace(" ", "")

    def found(form: str) -> bool:
        f = normalise(form)
        return f in heard or (len(f.replace(" ", "")) >= 5 and f.replace(" ", "") in squashed)

    return [term for term, forms in terms.items() if not any(found(f) for f in forms)]


# -- voicing and listening --------------------------------------------------------------------


def load_speakable(path: str | None):
    if not path:
        from showntell.pronounce import speakable

        return speakable
    spec = importlib.util.spec_from_file_location("pronounce_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod.speakable


def caption_coverage(display: str, words: list[dict]) -> float:
    """Share of the display's words that some timed word lands on."""
    chunks = [m.span() for m in re.finditer(r"\S+", display)]
    hit = sum(1 for a, b in chunks if any(w["c0"] < b and a < w["c1"] for w in words))
    return hit / max(1, len(chunks))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--data-dir",
        default=str(Path.home() / ".local/share/showntell"),
        help="showntell data folder, where the spaCy model is kept (default: %(default)s)",
    )
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--pronounce", help="load speakable() from this file instead, e.g. an older pronounce.py")
    ap.add_argument("--whisper", default=WHISPER, help="Whisper model repo (default: %(default)s)")
    ap.add_argument(
        "--confirm-with",
        metavar="REPO",
        help="a second Whisper model; also report the terms both models miss, which "
        "separates speech-recognition slips from real mispronunciations "
        "(e.g. mlx-community/whisper-small-mlx)",
    )
    ap.add_argument("-k", dest="filter", help="only cases whose text contains this (case-insensitive)")
    ap.add_argument("--save-audio", help="also write each clip as a WAV file into this folder")
    ap.add_argument("--json", help="write per-case results to this file")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every case, not only failures")
    args = ap.parse_args()

    import mlx_whisper
    from scipy.signal import resample_poly

    from showntell.speech.mlx_backend import MlxBackend

    speakable = load_speakable(args.pronounce)
    # a British voice gets British phonemes, when this speakable() can give them
    british = args.voice.startswith("b") and "british" in inspect.signature(speakable).parameters
    extra = {"british": True} if british else {}
    cases = [c for c in CASES if not args.filter or args.filter.lower() in c[0].lower()]
    backend = MlxBackend(Path(args.data_dir).expanduser())
    t0 = time.time()
    backend.load()
    backend.synth("Ready.", args.voice, 1.0)
    print(f"Kokoro ready in {time.time() - t0:.1f}s; {len(cases)} cases, {sum(len(t) for _, t in cases)} terms", file=sys.stderr)
    out_dir = Path(args.save_audio) if args.save_audio else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    total_s = 0.0
    failed: list[tuple[str, str]] = []
    confirmed: list[tuple[str, str]] = []  # missed by the second model too
    results = []
    worst_cover = 1.0
    for i, (src, terms) in enumerate(cases):
        n = parse_narration(src)
        spoken = speakable(n.display, n.code, n.say, {}, **extra)
        audio, tokens = backend.synth(spoken.text, args.voice, 1.0)
        seconds = len(audio) / SAMPLE_RATE
        total_s += seconds
        cover = caption_coverage(n.display, timed_words(spoken, tokens))
        worst_cover = min(worst_cover, cover)
        if out_dir:
            import soundfile as sf

            sf.write(out_dir / f"{i:02d}.wav", np.clip(audio, -1, 1), SAMPLE_RATE)
        clip = resample_poly(audio.astype(np.float32), 2, 3).astype(np.float32)  # 24 kHz -> 16 kHz
        heard = mlx_whisper.transcribe(clip, path_or_hf_repo=args.whisper, language="en", condition_on_previous_text=False)["text"].strip()
        missed = check(heard, terms)
        failed += [(t, src) for t in missed]
        second = ""
        if missed and args.confirm_with:
            second = mlx_whisper.transcribe(clip, path_or_hf_repo=args.confirm_with, language="en", condition_on_previous_text=False)[
                "text"
            ].strip()
            both = [t for t in check(second, terms) if t in missed]
            confirmed += [(t, src) for t in both]
        results.append(
            {
                "text": src,
                "spoken": spoken.text,
                "heard": heard,
                "missed": missed,
                "heard_again": second,
                "seconds": round(seconds, 2),
                "caption_coverage": round(cover, 3),
            }
        )
        if missed or args.verbose:
            print(
                f"\n[{i}] {src}\n  spoken: {spoken.text}\n  heard:  {heard}"
                + (f"\n  again:  {second}" if second else "")
                + (f"\n  MISSED: {', '.join(missed)}" if missed else "")
            )

    minutes = total_s / 60
    n_terms = sum(len(t) for _, t in cases)
    print(
        f"\n{len(failed)} of {n_terms} terms missed in {minutes:.2f} min of audio: "
        f"{len(failed) / max(minutes, 1e-9):.2f} per minute (target: at most 1). "
        f"Worst caption coverage {worst_cover:.0%}."
    )
    if failed:
        print("Missed: " + ", ".join(t for t, _ in failed))
    if args.confirm_with:
        print(
            f"Missed by {args.confirm_with} too: {len(confirmed)}, "
            f"{len(confirmed) / max(minutes, 1e-9):.2f} per minute" + (": " + ", ".join(t for t, _ in confirmed) if confirmed else "")
        )
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"minutes": round(minutes, 3), "missed": len(failed), "confirmed": len(confirmed), "terms": n_terms, "cases": results},
                indent=1,
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
