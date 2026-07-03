#!/usr/bin/env bash
# Enable BBR congestion control and apply network/kernel tuning for throughput.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

msg "Applying BBR + network tuning"
cat > /etc/sysctl.d/99-zeta.conf <<'CONF'
# Managed by ZetaVPN — performance tuning
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_fastopen = 3
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.ipv4.tcp_rmem = 4096 87380 67108864
net.ipv4.tcp_wmem = 4096 65536 67108864
net.ipv4.tcp_mtu_probing = 1
net.ipv4.ip_forward = 1
net.core.somaxconn = 4096
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.tcp_slow_start_after_idle = 0
fs.file-max = 1000000
CONF

sysctl --system >/dev/null 2>&1 || sysctl -p /etc/sysctl.d/99-zeta.conf >/dev/null 2>&1 || true

CC="$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || echo unknown)"
if [ "$CC" = "bbr" ]; then
  ok "BBR active (qdisc: $(sysctl -n net.core.default_qdisc 2>/dev/null))"
else
  warn "BBR not active (current: ${CC}). A reboot may be required on older kernels."
fi

# Raise open-file limits for the proxy cores.
if ! grep -q 'ZetaVPN limits' /etc/security/limits.conf 2>/dev/null; then
  cat >> /etc/security/limits.conf <<'CONF'
# ZetaVPN limits
* soft nofile 1000000
* hard nofile 1000000
CONF
fi
ok "Network tuning applied"
