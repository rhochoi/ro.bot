#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/git-tidy.sh [--apply]
  (default): show what would be cleaned; changes nothing except `git fetch --prune`
  --apply:   delete local branches already in origin/main (merged, same patches, or the head of a merged PR),
             remove their clean worktrees, prune stale worktrees, fast-forward local main
  --help:    show this help
Never deletes remote branches or tags. Branches with commits not in origin/main are only listed:
tag them `archive/<name>` and delete them by hand (docs/GIT-WORKFLOW.md).
EOF
}

apply=false
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --apply) apply=true ;;
    "") ;;
    *) usage >&2; exit 2 ;;
esac

git fetch --prune --quiet origin
base=origin/main
current=$(git symbolic-ref --quiet --short HEAD || true)

# 브랜치 → 체크아웃된 워크트리 경로 (bash 3.2 호환: 연관 배열 없이 줄 목록)
worktrees=$(git worktree list --porcelain | awk '/^worktree /{w=substr($0,10)} /^branch refs\/heads\//{print substr($0,19) "\t" w}')
worktree_of() { printf '%s\n' "$worktrees" | awk -F'\t' -v b="$1" '$1==b{print $2; exit}'; }

# 커밋 여러 개를 스쿼시 머지하면 패치가 달라져 git만으로는 못 알아본다 → 브랜치 끝이 머지된 PR의 head인지 gh로 확인
squash_merged() {
    command -v gh >/dev/null 2>&1 || return 1
    [ "$(gh pr list --head "$1" --state merged --json headRefOid --jq '.[].headRefOid' 2>/dev/null | head -1)" = "$(git rev-parse "$1")" ]
}

# 워크트리는 진행 중인 세션일 수 있다(새 세션의 빈 브랜치도 main에 '들어간' 것으로 보인다) → 머지된 PR이 있을 때만 치운다
pr_merged() {
    command -v gh >/dev/null 2>&1 || return 1
    [ -n "$(gh pr list --head "$1" --state merged --json number --jq '.[].number' 2>/dev/null | head -1)" ]
}

done_list=""; keep_list=""
for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do
    [ "$b" = main ] && continue
    if git merge-base --is-ancestor "$b" "$base" || ! git cherry "$base" "$b" | grep -q '^+' || squash_merged "$b"; then
        done_list="$done_list $b"
    else
        keep_list="$keep_list $b"
    fi
done

echo "== origin/main에 이미 들어간 브랜치 (정리 대상)"
for b in $done_list; do
    w=$(worktree_of "$b")
    note=""
    [ "$b" = "$current" ] && note=" (지금 체크아웃됨: 건너뜀)"
    [ -n "$w" ] && [ "$b" != "$current" ] && note=" (워크트리: $w)"
    echo "  $b$note"
done
echo "== main에 없는 커밋이 있는 브랜치 (보기만: archive/ 태그 후 직접 정리)"
for b in $keep_list; do echo "  $b  +$(git cherry "$base" "$b" | grep -c '^+')"; done
behind=$(git rev-list --count "main..$base" 2>/dev/null || echo 0)
echo "== 로컬 main: origin/main보다 ${behind}커밋 뒤"

$apply || { echo; echo "보기만 했습니다. 정리하려면: bash scripts/git-tidy.sh --apply"; exit 0; }

echo; echo "== 정리"
git worktree prune
for b in $done_list; do
    [ "$b" = "$current" ] && continue
    w=$(worktree_of "$b")
    if [ -n "$w" ] && [ -d "$w" ]; then
        if ! pr_merged "$b"; then
            echo "  건너뜀 $b: 머지된 PR이 없는 워크트리(진행 중인 세션일 수 있음, $w)"; continue
        fi
        if [ -n "$(git -C "$w" status --porcelain 2>/dev/null)" ]; then
            echo "  건너뜀 $b: 워크트리에 커밋 안 된 변경 ($w)"; continue
        fi
        git worktree remove "$w" || { echo "  실패 $b: 워크트리 제거 ($w)"; continue; }
        echo "  워크트리 제거 $w"
    fi
    if git branch -D "$b" >/dev/null; then echo "  삭제 $b"; else echo "  실패 $b"; fi
done
if [ "$behind" != 0 ] && [ "$current" != main ]; then
    if git fetch --quiet origin main:main 2>/dev/null; then echo "  로컬 main 빨리 감기"
    else echo "  로컬 main 빨리 감기 실패(다른 워크트리에서 체크아웃 중이거나 갈라짐)"; fi
fi
