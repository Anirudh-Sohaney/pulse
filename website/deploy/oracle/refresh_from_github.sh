#!/usr/bin/env bash
set -Eeuo pipefail

repo=${PULSE_DEPLOY_REPO:-/opt/pulse}
branch=${PULSE_DEPLOY_BRANCH:-codex/production-readiness}
state_dir=${PULSE_DEPLOY_STATE_DIR:-/var/lib/pulse}
state_file=$state_dir/deployed-commit
rejected_file=$state_dir/rejected-deploy-commit
source_path=data/targeted_additions/cms_partd_geography_drug/data/arkansas_partd_geography_drug_by_year.csv.gz

exec 9>"${PULSE_DEPLOY_LOCK_FILE:-/run/lock/pulse-update.lock}"
flock -n 9 || exit 0

cd "$repo"
if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
  echo 'Tracked files changed on the server; refusing to overwrite them' >&2
  exit 1
fi

git fetch --prune origin "+refs/heads/$branch:refs/remotes/origin/$branch"
current=$(git rev-parse HEAD)
target=$(git rev-parse "origin/$branch")
deployed=$(cat "$state_file" 2>/dev/null || true)
export PULSE_REVISION=$target
if [[ $(cat "$rejected_file" 2>/dev/null || true) == "$target" ]]; then
  echo "Revision $target previously failed after container startup; waiting for a new commit or operator retry" >&2
  exit 0
fi
if ! git merge-base --is-ancestor "$current" "$target"; then
  echo 'GitHub branch is not a fast-forward from the deployed checkout' >&2
  exit 1
fi
if [[ "$current" != "$target" ]]; then
  git merge --ff-only "$target"
fi

if [[ "$deployed" == "$target" ]]; then
  echo "Already deployed $target"
  exit 0
fi

compose=(docker compose -f docker-compose.yml -f docker-compose.cloud.yml -f docker-compose.oracle.yml)
services=(backend worker backup frontend data-permissions)
rollback_tags=()
containers_touched=false
rollback_on_failure() {
  local result=$?
  trap - EXIT
  if (( result != 0 )) && [[ -n "$deployed" && "$deployed" != "$target" ]] &&
     git -C "$repo" cat-file -e "$deployed^{commit}" 2>/dev/null; then
    echo "Deployment failed; restoring checkout $deployed" >&2
    git -C "$repo" reset --hard "$deployed" >&2 || true
    export PULSE_REVISION=$deployed
    if [[ "$containers_touched" == true && ${#rollback_tags[@]} -eq ${#services[@]} ]]; then
      local service index
      for index in "${!services[@]}"; do
        service=${services[$index]}
        docker image tag "${rollback_tags[$index]}" "website-$service:latest" || true
      done
      cd "$repo/website"
      if "${compose[@]}" up -d --no-build --remove-orphans; then
        for attempt in $(seq 1 30); do
          if curl --fail --silent --max-time 5 http://127.0.0.1:8000/ready >/dev/null &&
             curl --fail --silent --max-time 5 http://127.0.0.1:3141/ >/dev/null &&
             [[ $(docker inspect -f '{{.State.Health.Status}}' website-worker-1) == healthy ]] &&
             [[ $(docker inspect -f '{{.State.Health.Status}}' website-backup-1) == healthy ]]; then
            printf '%s\n' "$target" > "$rejected_file"
            echo "Restored $deployed; quarantined failed revision $target" >&2
            break
          fi
          sleep 5
        done
      fi
    fi
  fi
  exit "$result"
}
trap rollback_on_failure EXIT

docs_only=false
if [[ -n "$deployed" ]] && git cat-file -e "$deployed^{commit}" 2>/dev/null; then
  docs_only=true
  while IFS= read -r changed; do
    case "$changed" in
      README.md|website/README.md|website/PRODUCTION_READINESS.md|website/docs.md|website/deploy/oracle/README.md|model/tests/*) ;;
      *) docs_only=false; break ;;
    esac
  done < <(git diff --name-only "$deployed" "$target")
fi

if [[ "$docs_only" == true ]]; then
  echo 'Revision changes only documentation or research tests; retaining the running containers'
else
  # Only hydrate the research artifact needed by the live CMS refresh.
  git lfs pull --include="$source_path" --exclude=''
  cd "$repo/website"
  python3 -m scripts.cloud_preflight
  "${compose[@]}" config --quiet
  if [[ -n "$deployed" ]]; then
    for service in "${services[@]}"; do
      tag="pulse-rollback-$service:${deployed:0:12}"
      docker image tag "website-$service:latest" "$tag"
      rollback_tags+=("$tag")
    done
  fi
  "${compose[@]}" build backend worker backup frontend
  # Compose can deduplicate the identical backend and initializer builds without
  # tagging the initializer image. Build it in a separate invocation.
  "${compose[@]}" build data-permissions
  containers_touched=true
  "${compose[@]}" up -d --no-build --remove-orphans
fi

for attempt in $(seq 1 60); do
  if curl --fail --silent --max-time 5 http://127.0.0.1:8000/ready >/dev/null &&
     curl --fail --silent --max-time 5 http://127.0.0.1:3141/ >/dev/null &&
     [[ $(docker inspect -f '{{.State.Health.Status}}' website-worker-1) == healthy ]] &&
     [[ $(docker inspect -f '{{.State.Health.Status}}' website-backup-1) == healthy ]] &&
     [[ $(docker inspect -f '{{.State.ExitCode}}' website-data-permissions-1) == 0 ]] &&
     curl --fail --silent --max-time 5 --resolve \
       pulseintelligence.tech:443:127.0.0.1 \
       https://pulseintelligence.tech/ >/dev/null &&
     curl --fail --silent --max-time 5 --resolve \
       pulseintelligence.tech:443:127.0.0.1 \
       https://pulseintelligence.tech/api/v1/signals/coverage/summary >/dev/null &&
     curl --fail --silent --max-time 5 --resolve \
       pulseintelligence.tech:443:127.0.0.1 \
       https://pulseintelligence.tech/browser_forecast.py >/dev/null &&
     ( [[ "$docs_only" == true ]] ||
       curl --fail --silent --max-time 5 --resolve \
         pulseintelligence.tech:443:127.0.0.1 \
         https://pulseintelligence.tech/api/v1/system/version |
         grep -Fq "\"runtime_revision\":\"$target\"" ); then
    # Validate the restored catalog and public signal contract against the
    # running data before declaring this revision deployed. A failed audit
    # triggers the same checkout and container rollback as a failed startup.
    docker exec website-backend-1 python -m scripts.audit_signal_store
    docker exec website-backend-1 python -m scripts.audit_live_signal_api \
      --base-url http://127.0.0.1:8000
    install -d -m 0700 "$state_dir"
    printf '%s\n' "$target" > "$state_file.tmp"
    mv "$state_file.tmp" "$state_file"
    rm -f "$rejected_file"
    for tag in "${rollback_tags[@]}"; do
      docker image rm "$tag" >/dev/null 2>&1 || true
    done
    echo "Deployed $target"
    exit 0
  fi
  sleep 5
done

echo 'Deployment did not become healthy within five minutes' >&2
docker ps >&2
exit 1
