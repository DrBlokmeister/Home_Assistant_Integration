#!/usr/bin/env bash
set -euo pipefail

repository="${1:?Fork repository required}"
base_branch="${2:?Default branch required}"
dry_run="${3:?Dry-run flag required}"
upstream=OpenEPaperLink/Home_Assistant_Integration

summary() {
    printf '%s\n' "$*" | tee -a "$GITHUB_STEP_SUMMARY"
}

case "$dry_run" in
    true|false) ;;
    *) summary "Invalid dry-run flag."; exit 1 ;;
esac

tag=$(gh api "repos/$upstream/releases/latest" --jq .tag_name)
if [[ ! "$tag" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    summary "The latest official release has an unsupported tag format. Review it manually."
    exit 1
fi

git fetch --no-tags "https://github.com/$upstream.git" "refs/tags/$tag"
release_commit=$(git rev-parse 'FETCH_HEAD^{commit}')
summary "Official release [$tag](https://github.com/$upstream/releases/tag/$tag) at $release_commit."

if git merge-base --is-ancestor "$release_commit" HEAD; then
    summary "This release is already included in the fork default branch."
    exit 0
fi

common_base=$(git merge-base HEAD "$release_commit")
workflow_changes=$(git diff --name-only "$common_base" "$release_commit" -- .github/workflows)
if [[ -n "$workflow_changes" ]]; then
    summary "This release changes GitHub Actions workflows and needs a manually reviewed merge."
    printf '\n```\n%s\n```\n' "$workflow_changes" >> "$GITHUB_STEP_SUMMARY"
    exit 1
fi

merge_status=0
merge_result=$(git merge-tree --write-tree --name-only --no-messages HEAD "$release_commit") || merge_status=$?
case "$merge_status" in
    0) merge_summary="The local merge preflight found no conflicts." ;;
    1)
        conflict_paths=$(printf '%s\n' "$merge_result" | tail -n +2)
        merge_summary=$(printf 'The local merge preflight found conflicts.\n\n```\n%s\n```' "$conflict_paths")
        ;;
    *) summary "The local merge preflight failed with exit code $merge_status."; exit "$merge_status" ;;
esac
summary "$merge_summary"

branch="upstream-release/$tag-${release_commit:0:12}"
if [[ "$dry_run" == true ]]; then
    summary "Dry run. The proposed branch is $branch."
    exit 0
fi

existing_pr=$(gh api --method GET "repos/$repository/pulls" \
    -f state=all -f base="$base_branch" -f head="${repository%%/*}:$branch" --jq '.[0].html_url // empty')
if [[ -n "$existing_pr" ]]; then
    summary "An update PR already exists for this release commit. $existing_pr"
    exit 0
fi

git push origin "$release_commit:refs/heads/$branch"
pr_body=$(cat <<EOF
Official release [$tag](https://github.com/$upstream/releases/tag/$tag) at $release_commit.

$merge_summary

Review the upstream changes and approve the PR workflows if GitHub requests approval. The merge preflight does not execute integration tests. After the required checks and release validation pass, use a merge commit to preserve upstream ancestry.

Publishing a fork release and installing it in Home Assistant are manual steps.
EOF
)
pr_url=$(gh pr create --repo "$repository" --base "$base_branch" --head "$branch" --draft \
    --title "Merge official OpenEPaperLink $tag" --body "$pr_body")
summary "Opened a draft update PR. $pr_url"
