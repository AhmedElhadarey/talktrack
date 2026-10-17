# Third-party notices

showntell bundles these libraries in `src/showntell/static/vendor/`, unmodified:

| Library | Version | Licence | Source |
|---|---|---|---|
| **Mermaid** | 11.4.1 | MIT | https://github.com/mermaid-js/mermaid |
| **KaTeX** | 0.16.11 | MIT | https://github.com/KaTeX/KaTeX |
| **highlight.js** | 11.10.0 | BSD-3-Clause | https://github.com/highlightjs/highlight.js |
| **Rough.js** | 4.6.6 | MIT | https://github.com/rough-stuff/rough |
| **Virgil** (font) | 3d4c829 | OFL-1.1 | https://github.com/excalidraw/virgil |

showntell downloads these models on first run; they are not part of the package:

| Model | Licence | Source |
|---|---|---|
| **Kokoro-82M** (weights and voices) | Apache-2.0 | https://huggingface.co/hexgrad/Kokoro-82M, via mlx-community/Kokoro-82M-bf16 and onnx-community/Kokoro-82M-v1.0-ONNX-timestamped |
| **spaCy en_core_web_sm** 3.8.0 | MIT | https://github.com/explosion/spacy-models |

Runtime dependencies are installed from PyPI under their own licences. The optional `espeak` extra installs espeak-ng (GPL-3.0); it is not installed by default.

The full licence texts of the bundled libraries are in `src/showntell/static/vendor/LICENSES/`.
