#!/usr/bin/env bash
# Every commit here is the owner's or a bot's, under a GitHub noreply address. Signing in as
# another of the owner's accounts is the mistake this catches: its address is noreply too.
#
#   check-identity.sh <range>   every commit in the range, and its Co-authored-by trailers
#   check-identity.sh           the identity the next commit would take
#
# GitHub's own committer is allowed for merges it performs; an AI model (Claude, Codex) may
# be credited as co-author.
set -eu

OWNER='5214595\+ren-diao@users\.noreply\.github\.com'
BOT='[0-9]+\+[a-z0-9-]+\[bot\]@users\.noreply\.github\.com'
ALLOWED_AUTHOR="^($OWNER|$BOT)$"
ALLOWED_COMMITTER="^($OWNER|$BOT|noreply@github\.com)$"
ALLOWED_COAUTHOR="^($OWNER|$BOT|[^@]+@anthropic\.com|[^@]+@openai\.com)$"

fail=0
report() {  # role, email, where
  printf '  %s email %s is not permitted (%s)\n' "$1" "$2" "$3" >&2
  fail=1
}

if [ $# -eq 0 ]; then
  a=$(git var GIT_AUTHOR_IDENT | sed -n 's/.*<\(.*\)>.*/\1/p')
  c=$(git var GIT_COMMITTER_IDENT | sed -n 's/.*<\(.*\)>.*/\1/p')
  printf '%s\n' "$a" | grep -Eq "$ALLOWED_AUTHOR" || report author "$a" "this commit"
  printf '%s\n' "$c" | grep -Eq "$ALLOWED_COMMITTER" || report committer "$c" "this commit"
else
  commits=$(git log --format='%h %ae %ce' "$1")   # a bad range must exit, not read nothing
  while read -r sha a c; do
    [ -z "$sha" ] && continue
    printf '%s\n' "$a" | grep -Eq "$ALLOWED_AUTHOR" || report author "$a" "$sha"
    printf '%s\n' "$c" | grep -Eq "$ALLOWED_COMMITTER" || report committer "$c" "$sha"
    for co in $(git log -1 --format='%(trailers:key=Co-authored-by,valueonly)' "$sha" | sed -n 's/.*<\(.*\)>.*/\1/p'); do
      printf '%s\n' "$co" | grep -Eq "$ALLOWED_COAUTHOR" || report co-author "$co" "$sha"
    done
  done <<EOF
$commits
EOF
fi

if [ "$fail" -ne 0 ]; then
  cat >&2 <<'WHY'

Set this repository's own identity and commit again:
  git config user.email '5214595+ren-diao@users.noreply.github.com'
  git commit --amend --reset-author   # if the commit is already made
WHY
  exit 1
fi
