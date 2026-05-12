#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

require_clean_tree() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Working tree has uncommitted changes."
    echo "Commit or stash them before creating a release tag."
    exit 1
  fi
}

normalize_version() {
  local version="$1"
  if [[ "$version" != v* ]]; then
    version="v$version"
  fi
  printf '%s' "$version"
}

confirm() {
  local prompt="$1"
  local answer
  read -r -p "$prompt [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

main() {
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required"
    exit 1
  fi

  local branch
  branch="$(git branch --show-current)"
  echo "Current branch: $branch"

  if [[ "$branch" != "main" ]]; then
    if ! confirm "You are not on main. Continue anyway?"; then
      exit 1
    fi
  fi

  require_clean_tree

  git fetch --prune origin main
  git fetch --force origin 'refs/tags/v*:refs/tags/v*'

  local latest_tag
  latest_tag="$(git tag --list 'v*' --sort=-v:refname | head -n 1 || true)"
  if [[ -n "$latest_tag" ]]; then
    echo "Latest release tag: $latest_tag"
  else
    echo "No existing release tags found."
  fi

  local input_version
  read -r -p "Release version (example v1.0.0): " input_version
  if [[ -z "$input_version" ]]; then
    echo "Release version is required"
    exit 1
  fi

  local version
  version="$(normalize_version "$input_version")"

  if git rev-parse "$version" >/dev/null 2>&1 || git ls-remote --tags origin "refs/tags/$version" | grep -q .; then
    echo "Tag $version already exists locally or on origin"
    exit 1
  fi

  echo
  echo "About to create release $version"
  echo "- Branch: $branch"
  echo "- Remote: origin"
  echo "- Repo: https://github.com/YoruDev-Ryland/RoofObserver"
  echo

  if ! confirm "Push the current branch and create the tag?"; then
    exit 1
  fi

  git push origin "$branch"
  git tag -a "$version" -m "Release $version"
  git push origin "$version"

  cat <<EOF

Release tag pushed: $version

GitHub Actions will now build the installer and publish it to a GitHub Release.

Watch progress:
  https://github.com/YoruDev-Ryland/RoofObserver/actions

Releases page:
  https://github.com/YoruDev-Ryland/RoofObserver/releases

Notes:
- This workflow does not upload Actions artifacts, so artifact storage is not consumed per build.
- The installer is attached directly to the GitHub Release instead.
EOF
}

main "$@"