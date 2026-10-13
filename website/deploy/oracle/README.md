# Oracle deployment runbook

The production host is `ssh dev_server`. The public origin is
`https://pulseintelligence.tech`; `www` redirects to the apex. The checkout is
`/opt/pulse` on branch `codex/production-readiness`. The tracked Compose and
Nginx configurations are in this directory and its parent `website/`.

## Release path

Commit and push tested changes to `codex/production-readiness`. The enabled
`pulse-update.timer` checks GitHub every five minutes and runs
`refresh_from_github.sh` as `ubuntu`. It refuses dirty tracked files or a
non-fast-forward branch, hydrates the pinned CMS Git LFS source, runs cloud
preflight, builds and starts Compose, waits for the API and frontend, then
writes `/var/lib/pulse/deployed-commit`. For an immediate update:

```bash
ssh dev_server 'sudo systemctl start pulse-update.service'
ssh dev_server 'git -C /opt/pulse rev-parse HEAD; cat /var/lib/pulse/deployed-commit'
```

The host Nginx site, proxy-header include, firewall unit, Certbot renewal hook,
and systemd update units are installed separately from container updates.
Changes to those tracked host files require a deliberate host installation and
Nginx/systemd validation before the tracked commit can be considered fully
deployed. The update service cannot modify root-owned host configuration.

## Runtime checks

```bash
ssh dev_server 'sudo docker ps'
ssh dev_server 'sudo docker exec website-worker-1 python -m scripts.operational_status sources'
ssh dev_server 'sudo docker exec website-backup-1 python -m scripts.operational_status backup'
ssh dev_server 'sudo nginx -t -q && sudo systemctl is-active nginx docker pulse-update.timer certbot.timer pulse-firewall.service'
ssh dev_server 'sudo certbot renew --dry-run'
```

`backend`, `worker`, `backup`, and `frontend` use Compose restart policies.
Docker and the update timer are enabled at boot. The worker refreshes public
sources at 16:00 UTC and retries due failures without replacing successful
values. The backup worker creates a verified snapshot at 04:00 UTC and retries
failed exports hourly. Check `/api/v1/signals/freshness` for source periods and
last attempts; a healthy worker heartbeat alone does not prove current source
data. The public API and frontend are bound to loopback ports 8000 and 3141
behind host Nginx; only 80, 443, and SSH are open externally.

```bash
curl -fsS https://pulseintelligence.tech/api/v1/signals/coverage/summary
curl -fsS 'https://pulseintelligence.tech/api/v1/signals/demand/drugs?limit=1'
curl -fsS https://pulseintelligence.tech/browser_forecast.py >/dev/null
```

## Backups and recovery

Unencrypted daily snapshots are under `/opt/pulse/website/data/backups` and
encrypted bundles are under `/mnt/data/pulse-encrypted-backups` on a separate
Oracle block volume. `PULSE_OFFSITE_BACKUP_DIR` is the Compose path variable;
the present mount is **not offsite**. The export uses a distinct 32-byte key in
`/opt/pulse/website/secrets/pulse_backup_key`. Do not place that key in Git,
the bundle directory, or an exported backup.

To rehearse the latest snapshot without modifying production data:

```bash
ssh dev_server 'sudo docker exec website-backend-1 python -m scripts.rehearse_restore /app/data/backups/SNAPSHOT_NAME'
```

`SNAPSHOT_NAME` is the `snapshot` value from `scripts.operational_status backup`.
The rehearsal verifies SQLite integrity, archived signal rows, the CMS source
pair, the ranking API, and account registration/login in a temporary copy.
The encrypted export is authenticated by the backup health check. To survive
loss of the entire Oracle host, provision independent storage for encrypted
bundles and retain the backup key separately, then rehearse recovery from that
copy. This independent copy is not yet configured.

Production secrets are in ignored files under `website/secrets/`; the ignored
`website/.env` has the domain and backup mount configuration. Keep both out of
Docker image contexts and repository commits. Current images have neither
`/app/.env` nor `/app/secrets`; `.dockerignore` enforces that boundary.
