#!/usr/bin/env bash
# Burn a pre-built house-style ASS onto a raw take MP4 (ffmpeg + libass).
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: burn_house_captions.sh <input.mp4> <captions.ass> <output.mp4>" >&2
  exit 2
fi

in_mp4=$1
ass=$2
out_mp4=$3

FFMPEG=${FFMPEG:-ffmpeg}
if ! "$FFMPEG" -filters 2>/dev/null | rg -q '\b(ass|subtitles)\b'; then
  if [[ -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg ]]; then
    FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
  fi
fi
if ! "$FFMPEG" -filters 2>/dev/null | rg -q '\b(ass|subtitles)\b'; then
  echo "ffmpeg needs libass (ass or subtitles filter). Try: brew install ffmpeg-full" >&2
  exit 1
fi

# Escape path for the subtitles filter (colons and quotes).
escaped=$(python3 -c "import sys; p=sys.argv[1]; print(p.replace('\\\\','\\\\\\\\').replace(':','\\\\:').replace(\"'\",\"\\\\'\"))" "$ass")

"$FFMPEG" -v error -y -i "$in_mp4" -vf "subtitles='${escaped}'" -map 0:v:0 -map 0:a:0 -c:v libx264 -crf 18 -preset medium -c:a copy "$out_mp4"
echo "Wrote $out_mp4"
