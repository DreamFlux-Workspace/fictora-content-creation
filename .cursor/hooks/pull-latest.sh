#!/bin/bash
# Fast-forward this checkout to origin/main when a session starts.

set -u

root="${CURSOR_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"
input=$(cat || true)
event=$(printf '%s' "$input" | python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
except Exception:
    data={}
print(data.get("hook_event_name", ""))
' 2>/dev/null || true)

emit() {
  EVENT="$event" MESSAGE="$1" python3 -c 'import json, os
message = os.environ["MESSAGE"]
event = os.environ.get("EVENT", "")
if event == "SessionStart":
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": message,
        }
    }
else:
    payload = {"additional_context": message}
print(json.dumps(payload))
' || printf '%s\n' '{"additional_context":"Pull finished. Local work was left in place."}'
}

if ! command -v git >/dev/null 2>&1; then
  emit "Pull skipped: git is not installed. Local files were left in place."
  exit 0
fi

if ! git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  emit "Pull skipped: ${root} is not a git checkout."
  exit 0
fi

branch=$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
if [[ "$branch" != "main" ]]; then
  emit "Pull skipped: checkout is on ${branch:-a detached HEAD}, not main. That branch was left as-is."
  exit 0
fi

export GIT_TERMINAL_PROMPT=0
pull_log=$(git -C "$root" pull --ff-only --no-rebase origin main 2>&1) || status=$?
status=${status:-0}
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
  emit "Pull skipped. Local edits would be overwritten, so this checkout stayed put."
elif printf '%s' "$pull_log" | grep -Eq 'Not possible to fast-forward|have diverged|diverging'; then
  emit "Pull skipped. main has local commits that are not on origin. Those commits stayed put."
else
  emit "Pull skipped. Local work on main was left in place."
fi
exit 0
