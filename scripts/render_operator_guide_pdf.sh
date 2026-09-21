#!/usr/bin/env bash
# Regenerate docs/content-ops/Content-Operator-Guide.pdf from the Markdown source.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MD="$ROOT/docs/content-ops/Content-Operator-Guide.md"
HTML="$ROOT/docs/content-ops/Content-Operator-Guide.html"
PDF="$ROOT/docs/content-ops/Content-Operator-Guide.pdf"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

pandoc "$MD" -o "$HTML" --standalone --metadata title="Fictora Content Operator Guide"
if [[ ! -x "$CHROME" ]]; then
  echo "Install Google Chrome or print $HTML to PDF manually." >&2
  exit 1
fi
"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PDF" "file://$HTML"
echo "Wrote $PDF"
