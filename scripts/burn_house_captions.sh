#!/usr/bin/env bash
# Burn a pre-built house-style ASS onto a raw take MP4 (ffmpeg + libass).
# Prefer `uv run fictora-produce caption --desk <desk>`, which builds the ASS too.
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: burn_house_captions.sh <input.mp4> <captions.ass> <output.mp4>" >&2
  exit 2
fi

in_mp4=$1
ass=$2
out_mp4=$3

# Capture the filter list before grepping: with pipefail, `grep -q` closing the
# pipe early makes ffmpeg exit on SIGPIPE and the check fails on a good build.
has_libass() {
  local filters
  filters=$("$1" -hide_banner -filters 2>/dev/null) || return 1
  grep -Eq '[[:space:]](ass|subtitles)[[:space:]]' <<<"$filters"
}

FFMPEG=${FFMPEG:-ffmpeg}
if ! has_libass "$FFMPEG"; then
  if [[ -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg ]]; then
    FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
  fi
fi
if ! has_libass "$FFMPEG"; then
  echo "ffmpeg needs libass (ass or subtitles filter). Try: brew install ffmpeg-full" >&2
  exit 1
fi

# Escape path for the subtitles filter (colons and quotes).
escape() {
  python3 -c "import sys; p=sys.argv[1]; print(p.replace('\\\\','\\\\\\\\').replace(':','\\\\:').replace(\"'\",\"\\\\'\"))" "$1"
}
escaped=$(escape "$ass")
# House font (Poppins Bold) ships with the repo so every laptop renders the same.
fonts=$(escape "$(cd "$(dirname "$0")/.." && pwd)/assets/fonts")

"$FFMPEG" -v error -y -i "$in_mp4" -vf "subtitles='${escaped}':fontsdir='${fonts}'" -map 0:v:0 -map "0:a:0?" -c:v libx264 -crf 18 -preset medium -c:a copy "$out_mp4"
echo "Wrote $out_mp4"
