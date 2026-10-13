#!/usr/bin/env bash
set -Eeuo pipefail
/usr/sbin/nginx -t
/usr/bin/systemctl reload nginx.service
