#!/usr/bin/env bash
# Replace the Linux screenshot baselines with the screenshots from a CI run.
#   scripts/update_baselines.sh            the latest CI run on main
#   scripts/update_baselines.sh RUN_ID     a given run
# Review the changed images (git diff --stat, then open them) before committing.
set -euo pipefail
cd "$(dirname "$0")/.."
run="${1:-$(gh run list --workflow CI --branch main --limit 1 --json databaseId --jq '.[0].databaseId')}"
tmp="$(mktemp -d)"
gh run download "$run" --name player-screenshots-linux --dir "$tmp"
mkdir -p tests/baselines/linux
find tests/baselines/linux -name '*.png' -delete
cp "$tmp"/landscape-*.png "$tmp"/square-*.png "$tmp"/portrait-*.png tests/baselines/linux/
rm -rf "$tmp"
echo "Baselines updated from CI run $run:"
git status --short tests/baselines/linux | head -40
