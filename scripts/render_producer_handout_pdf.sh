#!/usr/bin/env bash
# Regenerate docs/Content-Producer-Setup-Handout.pdf from the Markdown source.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MD="$ROOT/docs/Content-Producer-Setup-Handout.md"
HTML="$ROOT/docs/Content-Producer-Setup-Handout.html"
PDF="$ROOT/docs/Content-Producer-Setup-Handout.pdf"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

pandoc "$MD" -o "$HTML" --standalone --metadata title="Fictora Content Producer Setup"
if [[ ! -x "$CHROME" ]]; then
  echo "Install Google Chrome or print $HTML to PDF manually." >&2
  exit 1
fi
"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PDF" "file://$HTML"
echo "Wrote $PDF"
