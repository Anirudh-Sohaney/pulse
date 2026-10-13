#!/usr/bin/env bash
set -Eeuo pipefail

# Oracle's IPv4 image includes a final INPUT reject. Insert web accepts before
# it; Docker manages forwarding separately.
if ! iptables -C INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT 2>/dev/null; then
  iptables -I INPUT 1 -p tcp -m multiport --dports 80,443 -j ACCEPT
fi

# The image has an ACCEPT IPv6 INPUT policy. Keep only loopback, established
# connections, neighbor discovery, SSH, and the two public web ports available
# if a routable IPv6 address is assigned in the future.
ip6tables -N PULSE_INPUT6 2>/dev/null || true
ip6tables -F PULSE_INPUT6
ip6tables -A PULSE_INPUT6 -i lo -j ACCEPT
ip6tables -A PULSE_INPUT6 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -A PULSE_INPUT6 -p ipv6-icmp -j ACCEPT
ip6tables -A PULSE_INPUT6 -p udp -s fe80::/10 --dport 546 -j ACCEPT
ip6tables -A PULSE_INPUT6 -p tcp --dport 22 -j ACCEPT
ip6tables -A PULSE_INPUT6 -p tcp -m multiport --dports 80,443 -j ACCEPT
ip6tables -A PULSE_INPUT6 -j REJECT --reject-with icmp6-adm-prohibited
if ! ip6tables -C INPUT -j PULSE_INPUT6 2>/dev/null; then
  ip6tables -I INPUT 1 -j PULSE_INPUT6
fi
