#!/usr/bin/env bash
set -Eeuo pipefail
/usr/sbin/nginx -t -q
/usr/bin/systemctl reload nginx.service
