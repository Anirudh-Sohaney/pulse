#!/usr/bin/env bash
set -Eeuo pipefail

# Oracle's image includes a final INPUT reject. Insert these narrow accepts
# before it; Docker manages its own forwarding rules separately.
for family in iptables ip6tables; do
  if ! "$family" -C INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT 2>/dev/null; then
    "$family" -I INPUT 1 -p tcp -m multiport --dports 80,443 -j ACCEPT
  fi
done
