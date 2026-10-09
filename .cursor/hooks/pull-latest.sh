#!/bin/bash
# Fast-forward this checkout to origin/main when a session starts.
#
# Needs only bash and git: no python (on Windows, python3 is often the
# Microsoft Store stub). Every answer says what really happened; a skip
# never reads as a pull.

set -u

input=$(cat || true)
event=""
if printf '%s' "$input" | grep -Eq '"hook_event_name"[[:space:]]*:[[:space:]]*"SessionStart"'; then
  event="SessionStart"
fi

json_string() {
  # A JSON string literal of $1: backslash and quote escaped, tabs and line breaks as spaces,
  # other control characters dropped. sed and tr only, so bash 3.2 (macOS) parses it too.
  printf '"%s"' "$(printf '%s' "$1" | LC_ALL=C tr '\t\r\n' '   ' | LC_ALL=C tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
}

emit() {
  local message
  message=$(json_string "$1")
  if [[ "$event" == "SessionStart" ]]; then
    printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":%s}}\n' "$message"
  else
    printf '{"additional_context":%s}\n' "$message"
  fi
}

if ! command -v git >/dev/null 2>&1; then
  emit "Pull skipped: git is not installed. Local files were left in place."
  exit 0
fi

# The checkout this hook belongs to, wherever the session started: the editor's
# project dir when it is a checkout, else the checkout holding this script, else pwd.
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
root=""
for candidate in "${CURSOR_PROJECT_DIR:-}" "${CLAUDE_PROJECT_DIR:-}" "${here:-}" "$(pwd)"; do
  [[ -n "$candidate" ]] || continue
  top=$(git -C "$candidate" rev-parse --show-toplevel 2>/dev/null) || continue
  if [[ -n "$top" ]]; then
    root=$top
    break
  fi
done
if [[ -z "$root" ]]; then
  emit "Pull skipped: no git checkout found (looked at the project dir, ${here:-the folder of this hook} and $(pwd)). Local files were left in place."
  exit 0
fi

branch=$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
if [[ "$branch" != "main" ]]; then
  emit "Pull skipped: checkout is on ${branch:-a detached HEAD}, not main. That branch was left as-is."
  exit 0
fi

# A kit command still running (another session's fictora-produce / fictora-ops) keeps this
# checkout as it is: pulling now swaps the kit's files under that command, and the next
# `uv run` reinstalls the kit's launchers, which Windows refuses while one is running
# ("failed to remove file ... fictora-produce.exe: Access is denied", NOCLIP L-20261008-8).
running_kit() {
  local found=""
  if command -v tasklist.exe >/dev/null 2>&1; then
    found=$(tasklist.exe //FO CSV //NH 2>/dev/null | grep -io -E '"fictora-(produce|ops)\.exe"' | head -n 1 | tr -d '"')
  elif command -v pgrep >/dev/null 2>&1; then
    found=$(pgrep -fl 'fictora-(produce|ops)|creation[.]cli_(produce|ops)' 2>/dev/null | grep -v -E '^[0-9]+ (pgrep|grep)( |$)' | head -n 1 | grep -o -E 'fictora-(produce|ops)|creation[.]cli_(produce|ops)' | head -n 1)
  fi
  printf '%s' "$found"
}

busy=$(running_kit)
if [[ -n "$busy" ]]; then
  sha=$(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo "unknown")
  emit "Pull skipped: a kit command (${busy}) is still running, so this checkout stayed at ${sha}. Pulling under it could change the kit mid-command, and the next uv run could fail to replace the kit's launcher. Start a new session after it finishes to pull. If uv run says 'failed to remove file ... Access is denied', run the same command with uv run --no-sync."
  exit 0
fi

export GIT_TERMINAL_PROMPT=0
status=0
pull_log=$(git -C "$root" pull --ff-only --no-rebase origin main 2>&1) || status=$?
sha=$(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo "unknown")
subject=$(git -C "$root" log -1 --format=%s 2>/dev/null || true)

if [[ "$status" -eq 0 ]]; then
  if printf '%s' "$pull_log" | grep -Eq 'Already up to date|Already up-to-date'; then
    emit "This checkout already matches origin/main at ${sha}: ${subject}. Do not ask the operator to git pull."
  else
    emit "Fast-forwarded this checkout to origin/main at ${sha}: ${subject}. Do not ask the operator to git pull."
  fi
  exit 0
fi

if printf '%s' "$pull_log" | grep -q 'would be overwritten'; then
  emit "Pull skipped. Local edits would be overwritten, so this checkout stayed put at ${sha}."
elif printf '%s' "$pull_log" | grep -Eq 'Not possible to fast-forward|have diverged|diverging'; then
  emit "Pull skipped. main has local commits that are not on origin. Those commits stayed put at ${sha}."
else
  reason=$(printf '%s' "$pull_log" | tail -n 1)
  emit "Pull skipped (git said: ${reason:-no reason given}). This checkout stayed at ${sha}; ask the operator to run git pull if they need the latest kit."
fi
exit 0
