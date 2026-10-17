"""Settings from the environment, with defaults. Command-line flags override these."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"SHOWNTELL_{name}", default)


@dataclass
class Config:
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("PORT", "4747")))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA", str(Path.home() / ".local/share/showntell"))).expanduser())
    voice: str = field(default_factory=lambda: _env("VOICE", "af_heart"))
    speech: str = field(default_factory=lambda: _env("SPEECH", "auto"))  # auto, mlx, onnx, fake
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL", ""))
    open_browser: bool = field(default_factory=lambda: _env("OPEN") == "1")
    access_token: str = field(default_factory=lambda: _env("ACCESS_TOKEN", ""))
    finish_check: bool = field(default_factory=lambda: _env("FINISH_CHECK", "1") != "0")  # layout check at finish

    @property
    def loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    @property
    def base_url(self) -> str:
        return (self.public_url or f"http://localhost:{self.port}").rstrip("/")

    @property
    def lockfile(self) -> Path:
        return self.data_dir / "daemon.json"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def lexicon_file(self) -> Path:
        return self.data_dir / "lexicon.txt"
