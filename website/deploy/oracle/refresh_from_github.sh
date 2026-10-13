#!/usr/bin/env bash
set -Eeuo pipefail

repo=/opt/pulse
branch=codex/production-readiness
state_dir=/var/lib/pulse
state_file=$state_dir/deployed-commit
source_path=data/targeted_additions/cms_partd_geography_drug/data/arkansas_partd_geography_drug_by_year.csv.gz

exec 9>/run/lock/pulse-update.lock
flock -n 9 || exit 0

cd "$repo"
if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
  echo 'Tracked files changed on the server; refusing to overwrite them' >&2
  exit 1
fi

git fetch --prune origin "+refs/heads/$branch:refs/remotes/origin/$branch"
current=$(git rev-parse HEAD)
target=$(git rev-parse "origin/$branch")
if ! git merge-base --is-ancestor "$current" "$target"; then
  echo 'GitHub branch is not a fast-forward from the deployed checkout' >&2
  exit 1
fi
if [[ "$current" != "$target" ]]; then
  git merge --ff-only "$target"
fi

deployed=$(cat "$state_file" 2>/dev/null || true)
if [[ "$deployed" == "$target" ]]; then
  echo "Already deployed $target"
  exit 0
fi

# Only hydrate the research artifact needed by the live CMS refresh.
git lfs pull --include="$source_path" --exclude=''
cd "$repo/website"
python3 -m scripts.cloud_preflight
compose=(docker compose -f docker-compose.yml -f docker-compose.cloud.yml -f docker-compose.oracle.yml)
"${compose[@]}" config --quiet
"${compose[@]}" build backend worker backup frontend
"${compose[@]}" up -d --no-build --remove-orphans

for attempt in $(seq 1 60); do
  if curl --fail --silent --max-time 5 http://127.0.0.1:8000/ready >/dev/null &&
     curl --fail --silent --max-time 5 http://127.0.0.1:3141/ >/dev/null; then
    install -d -m 0700 "$state_dir"
    printf '%s\n' "$target" > "$state_file"
    echo "Deployed $target"
    exit 0
  fi
  sleep 5
done

echo 'Deployment did not become healthy within five minutes' >&2
"${compose[@]}" ps >&2
exit 1
