"""On Apple silicon, speech falls back to ONNX when MLX cannot load or speak."""

from __future__ import annotations

import pytest

from showntell.speech import fallback, make_backend, mlx_backend
from showntell.speech.fake import FakeBackend


class Broken(FakeBackend):
    name, model_id = "mlx", "mlx-model"

    def __init__(self, fail_in: str) -> None:
        super().__init__()
        self.fail_in = fail_in

    def load(self) -> None:
        if self.fail_in == "load":
            raise ImportError("mlx_audio moved load_model")

    def synth(self, text, voice, speed):
        if self.fail_in == "synth":
            raise TypeError("unexpected keyword argument return_output")
        return super().synth(text, voice, speed)


@pytest.mark.parametrize("fail_in", ["load", "synth"])
def test_a_broken_mlx_hands_over_to_onnx(fail_in):
    b = fallback.Fallback(Broken(fail_in), FakeBackend)
    b.load()
    audio, _ = b.synth("Hello there.", "af_heart", 1.0)
    assert b.name == FakeBackend.name and b.fell_back and len(audio)


def test_a_working_mlx_stays():
    b = fallback.Fallback(Broken(""), lambda: pytest.fail("must not load ONNX"))
    b.load()
    assert b.name == "mlx" and b.model_id == "mlx-model" and not b.fell_back


def test_when_both_fail_the_error_names_both():
    def onnx():
        raise RuntimeError("no onnxruntime")

    with pytest.raises(RuntimeError, match="MLX failed.*load_model.*ONNX.*no onnxruntime"):
        fallback.Fallback(Broken("load"), onnx).load()


def test_auto_on_apple_silicon_wraps_mlx(monkeypatch, tmp_path):
    from showntell.speech import Status

    monkeypatch.setattr(mlx_backend, "available", lambda: True)
    b = make_backend("auto", tmp_path, Status())
    assert isinstance(b, fallback.Fallback) and isinstance(b.active, mlx_backend.MlxBackend)
    assert isinstance(make_backend("mlx", tmp_path, Status()), mlx_backend.MlxBackend)  # asked for MLX: no fallback
