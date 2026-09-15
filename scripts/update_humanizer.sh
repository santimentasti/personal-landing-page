#!/usr/bin/env bash
# Re-vendor SKILL.md and LICENSE from blader/humanizer and record the commit.
set -euo pipefail
cd "$(dirname "$0")/humanizer"
REF="${1:-main}"
if [ "$REF" = "main" ]; then
  SHA="$(git ls-remote https://github.com/blader/humanizer.git refs/heads/main | cut -f1)"
else
  SHA="$REF"
fi
curl -fsSL "https://raw.githubusercontent.com/blader/humanizer/${SHA}/SKILL.md" -o SKILL.md
curl -fsSL "https://raw.githubusercontent.com/blader/humanizer/${SHA}/LICENSE" -o LICENSE
TODAY="$(date +%F)"
sed -i -E "s/Pinned commit: \`[0-9a-f]+\`/Pinned commit: \`${SHA}\`/; s/Vendored on: [0-9-]+/Vendored on: ${TODAY}/" NOTICE.md
echo "humanizer vendored at ${SHA}"
