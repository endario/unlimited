#!/usr/bin/env bash
# check-identity.sh against commits made in a scratch repository.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cd "$T" && git init -q && git config user.useConfigOnly true && git config user.name t
fail=0
commit() {  # email, message
  GIT_COMMITTER_EMAIL="$1" git -c user.email="$1" commit -q --allow-empty -m "$2"
}
expect() {  # pass|fail, what
  if bash "$HERE/check-identity.sh" HEAD~1..HEAD >/dev/null 2>&1; then got=pass; else got=fail; fi
  [ "$got" = "$1" ] && echo "  ok - $2" || { echo "  FAIL - $2 (got $got)"; fail=1; }
}
commit 5214595+ren-diao@users.noreply.github.com root
commit 5214595+ren-diao@users.noreply.github.com "$(printf 'x\n\nCo-authored-by: Claude Opus 5 <noreply@anthropic.com>')"
expect pass "the owner, with Claude as co-author"
commit 5214595+ren-diao@users.noreply.github.com "$(printf 'v\n\nCo-authored-by: Codex <codex@openai.com>')"
expect pass "the owner, with Codex as co-author"
commit 326013263+endario-ci[bot]@users.noreply.github.com y
expect pass "a bot"
commit 999+another-account@users.noreply.github.com z
expect fail "another of the owner's accounts, though its address is noreply"
commit 5214595+ren-diao@users.noreply.github.com "$(printf 'w\n\nCo-authored-by: q <999+another-account@users.noreply.github.com>')"
expect fail "another account credited as co-author"
exit $fail
