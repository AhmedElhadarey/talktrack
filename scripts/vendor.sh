#!/bin/zsh
# Download the pinned browser libraries into src/showntell/static/vendor, checking each
# tarball's sha256, so the player works offline.
set -euo pipefail
cd "${0:A:h}/.."
V=src/showntell/static/vendor
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fetch() { # name version sha256
  local name=$1 version=$2 sum=$3 base=${1##*/}
  mkdir -p "$TMP/$name"
  curl -fsSL "https://registry.npmjs.org/$name/-/$base-$version.tgz" -o "$TMP/$base.tgz"
  echo "$sum  $TMP/$base.tgz" | shasum -a 256 -c --quiet
  tar -xzf "$TMP/$base.tgz" -C "$TMP/$name"
}
rm -rf "$V/hljs" "$V/katex" "$V/mermaid.min.js"
mkdir -p "$V/katex/fonts" "$V/hljs" "$V/LICENSES"
fetch mermaid 11.4.1 ec7b61b391aaa24bf666fd20e48d0a626eeb4a95b95bc6b3016a122d7f0bc2af
cp "$TMP/mermaid/package/dist/mermaid.min.js" "$V/"
cp "$TMP/mermaid/package/LICENSE" "$V/LICENSES/mermaid.txt" 2>/dev/null || true
fetch katex 0.16.11 8c5494c5f7d8e8bf73ff3fe64d4bd09a340e4878b7eec9bbd9ef8d8f3e24193f
cp "$TMP/katex/package/dist/katex.min.js" "$TMP/katex/package/dist/katex.min.css" "$V/katex/"
cp "$TMP/katex/package/dist/fonts/"*.woff2 "$V/katex/fonts/"
cp "$TMP/katex/package/LICENSE" "$V/LICENSES/katex.txt"
fetch @highlightjs/cdn-assets 11.10.0 5df5f63aad9acb3660d0d008cd68bdf5a6b7121816f517e2434d1e2f027b220f
cp "$TMP/@highlightjs/cdn-assets/package/highlight.min.js" "$V/hljs/"
cp "$TMP/@highlightjs/cdn-assets/package/styles/github-dark.min.css" "$TMP/@highlightjs/cdn-assets/package/styles/github.min.css" "$V/hljs/"
cp "$TMP/@highlightjs/cdn-assets/package/LICENSE" "$V/LICENSES/highlight.js.txt"
du -sh "$V"
