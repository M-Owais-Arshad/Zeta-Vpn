#!/usr/bin/env bash
# ZetaVPN — Elite gaming / low-latency network tuning engine.
# ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0
#
# Usage (root only; the panel reaches it through zeta-privileged):
#   zeta-tuning apply     — snapshot current state, then apply elite tuning
#   zeta-tuning revert    — restore the server to its exact pre-tuning state
#   zeta-tuning status    — print "active" or "inactive"
#   zeta-tuning reapply    — re-apply the live (non-sysctl) bits on boot
#
# Design: everything that gets changed is first snapshotted under $SNAP so
# `revert` puts the box back exactly how it was. sysctls also persist via a
# drop-in; the non-sysctl bits (qdisc, MSS, txqueuelen) are re-applied on boot
# by a tiny oneshot unit. Nothing here opens a port or touches ufw/fail2ban.
set -uo pipefail

SNAP=/var/backups/zeta-tune
SYSCTL_D=/etc/sysctl.d/99-zeta-gaming.conf
BOOT_UNIT=/etc/systemd/system/zeta-gaming-tune.service
STATE="$SNAP/state"

iface() { ip route get 1.1.1.1 2>/dev/null | grep -Po 'dev \K\S+' | head -1; }

# Keys we set with sysctl -w and persist. Snapshot reads their originals.
SYSCTL_KEYS=(
  net.ipv4.tcp_congestion_control net.core.default_qdisc
  net.core.rmem_max net.core.wmem_max net.core.rmem_default net.core.wmem_default
  net.ipv4.tcp_rmem net.ipv4.tcp_wmem
  net.ipv4.udp_rmem_min net.ipv4.udp_wmem_min net.ipv4.udp_mem
  net.core.netdev_max_backlog net.core.somaxconn net.ipv4.tcp_max_syn_backlog
  net.ipv4.tcp_fastopen net.ipv4.tcp_notsent_lowat net.ipv4.tcp_mtu_probing
  net.ipv4.tcp_slow_start_after_idle net.ipv4.icmp_ratelimit
  net.ipv4.ip_local_port_range net.ipv4.tcp_tw_reuse net.ipv4.tcp_fin_timeout
)

snapshot() {
  mkdir -p "$SNAP/dropins"
  local IF; IF=$(iface); echo "${IF:-eth0}" > "$SNAP/iface.orig"
  # sysctl originals as "key = value" lines so revert is `sysctl -p`.
  : > "$SNAP/sysctl.orig"
  local k v
  for k in "${SYSCTL_KEYS[@]}"; do
    v=$(sysctl -n "$k" 2>/dev/null) || continue
    printf '%s = %s\n' "$k" "$v" >> "$SNAP/sysctl.orig"
  done
  [ -n "$IF" ] && {
    tc qdisc show dev "$IF" root 2>/dev/null > "$SNAP/qdisc.orig" || true
    cat "/sys/class/net/$IF/tx_queue_len" 2>/dev/null > "$SNAP/txqlen.orig" || true
    ethtool -a "$IF" 2>/dev/null > "$SNAP/pause.orig" || true
  }
  iptables-save -t mangle 2>/dev/null > "$SNAP/mangle.orig" || true
  { local i=0 g
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
      [ -r "$g" ] && echo "$(cat "$g")"; i=$((i+1))
    done
  } > "$SNAP/governor.orig" 2>/dev/null || true
  # existing drop-ins for units we touch
  local svc
  for svc in zeta-panel zeta-xray zeta-singbox zeta-ws; do
    [ -d "/etc/systemd/system/${svc}.service.d" ] && \
      cp -a "/etc/systemd/system/${svc}.service.d" "$SNAP/dropins/${svc}.d" 2>/dev/null || true
  done
}

write_sysctl_d() {
  cat > "$SYSCTL_D" <<'CONF'
# ZetaVPN elite gaming tuning (managed — removed by `zeta-tuning revert`)
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 262144
net.core.wmem_default = 262144
net.ipv4.tcp_rmem = 4096 131072 16777216
net.ipv4.tcp_wmem = 4096 16384 16777216
net.ipv4.udp_rmem_min = 16384
net.ipv4.udp_wmem_min = 16384
net.core.netdev_max_backlog = 16384
net.core.somaxconn = 8192
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_notsent_lowat = 16384
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.icmp_ratelimit = 0
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
CONF
}

# The live, non-sysctl bits (safe to run every boot; idempotent).
apply_live() {
  local IF; IF=$(iface); [ -z "$IF" ] && IF=$(cat "$SNAP/iface.orig" 2>/dev/null)
  modprobe tcp_bbr 2>/dev/null || true
  # sysctls (immediate)
  sysctl -p "$SYSCTL_D" >/dev/null 2>&1 || true
  # qdisc: fq (BBR's pacing engine)
  [ -n "$IF" ] && tc qdisc replace dev "$IF" root fq 2>/dev/null || true
  # MSS clamp for 4G/5G + tunnel headroom (idempotent: delete-then-add)
  if [ -n "$IF" ]; then
    iptables -t mangle -D FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
    iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
    iptables -t mangle -D POSTROUTING -p tcp --tcp-flags SYN,RST SYN -o "$IF" -j TCPMSS --set-mss 1360 2>/dev/null || true
    iptables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -o "$IF" -j TCPMSS --set-mss 1360 2>/dev/null || true
    ip link set dev "$IF" txqueuelen 2000 2>/dev/null || true
    # (ethtool -A pause frames intentionally NOT applied: they can't be exactly
    #  reverted on all NICs, and the whole feature promises a clean turn-off.)
  fi
  # CPU governor = performance (no-op on KVM guests with no cpufreq)
  local g
  for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [ -w "$g" ] && echo performance > "$g" 2>/dev/null || true
  done
}

write_prio_dropins() {
  local d
  # data plane: priority
  for svc in zeta-xray zeta-singbox zeta-badvpn; do
    d="/etc/systemd/system/${svc}.service.d"; mkdir -p "$d"
    printf '[Service]\nNice=-5\nCPUWeight=300\nIOWeight=200\n' > "$d/20-zeta-prio.conf"
  done
  # control plane: deprioritize the panel so it never steals CPU from forwarding
  d="/etc/systemd/system/zeta-panel.service.d"; mkdir -p "$d"
  printf '[Service]\nNice=10\nCPUWeight=50\nIOWeight=50\n' > "$d/20-zeta-deprio.conf"
  systemctl daemon-reload 2>/dev/null || true
  # Apply the cgroup weights to the ALREADY-RUNNING units immediately (a
  # drop-in + daemon-reload only affects the next start; --runtime set-property
  # takes effect live without dropping any connections).
  for svc in zeta-xray zeta-singbox zeta-badvpn; do
    systemctl set-property --runtime "$svc" CPUWeight=300 IOWeight=200 2>/dev/null || true
  done
  systemctl set-property --runtime zeta-panel CPUWeight=50 IOWeight=50 2>/dev/null || true
}

remove_prio_dropins() {
  rm -f /etc/systemd/system/zeta-xray.service.d/20-zeta-prio.conf \
        /etc/systemd/system/zeta-singbox.service.d/20-zeta-prio.conf \
        /etc/systemd/system/zeta-badvpn.service.d/20-zeta-prio.conf \
        /etc/systemd/system/zeta-panel.service.d/20-zeta-deprio.conf 2>/dev/null || true
  local svc
  for svc in zeta-xray zeta-singbox zeta-badvpn zeta-panel; do
    rmdir --ignore-fail-on-non-empty "/etc/systemd/system/${svc}.service.d" 2>/dev/null || true
  done
  systemctl daemon-reload 2>/dev/null || true
}

install_boot_unit() {
  cat > "$BOOT_UNIT" <<UNIT
[Unit]
Description=ZetaVPN gaming tuning (re-apply live network state on boot)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/zeta-tuning reapply
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload 2>/dev/null || true
  systemctl enable zeta-gaming-tune.service 2>/dev/null || true
}

cmd_apply() {
  mkdir -p "$SNAP"
  [ -f "$STATE" ] && { apply_live; echo "already active (re-applied)"; exit 0; }
  snapshot
  write_sysctl_d
  apply_live
  write_prio_dropins
  install_boot_unit
  echo active > "$STATE"
  echo "Elite gaming tuning applied."
}

cmd_revert() {
  [ -f "$STATE" ] || { echo "not active"; exit 0; }
  local IF; IF=$(cat "$SNAP/iface.orig" 2>/dev/null); [ -z "$IF" ] && IF=$(iface)
  # sysctls back to snapshot
  [ -f "$SNAP/sysctl.orig" ] && sysctl -p "$SNAP/sysctl.orig" >/dev/null 2>&1 || true
  rm -f "$SYSCTL_D"
  # qdisc back to original kind (default fq_codel if unknown)
  if [ -n "$IF" ]; then
    local kind; kind=$(awk '{print $2}' "$SNAP/qdisc.orig" 2>/dev/null); [ -z "$kind" ] && kind=fq_codel
    tc qdisc replace dev "$IF" root "$kind" 2>/dev/null || tc qdisc del dev "$IF" root 2>/dev/null || true
    # txqueuelen
    local txq; txq=$(cat "$SNAP/txqlen.orig" 2>/dev/null); [ -n "$txq" ] && ip link set dev "$IF" txqueuelen "$txq" 2>/dev/null || true
  fi
  # exact mangle-table restore (removes the MSS clamp rules atomically)
  [ -f "$SNAP/mangle.orig" ] && iptables-restore -T mangle < "$SNAP/mangle.orig" 2>/dev/null || true
  # CPU governor back
  if [ -f "$SNAP/governor.orig" ]; then
    local i=0 g v
    while IFS= read -r v; do
      g=$(ls -d /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | sed -n "$((i+1))p")
      [ -n "$g" ] && [ -w "$g" ] && [ -n "$v" ] && echo "$v" > "$g" 2>/dev/null || true
      i=$((i+1))
    done < "$SNAP/governor.orig"
  fi
  remove_prio_dropins
  systemctl disable --now zeta-gaming-tune.service 2>/dev/null || true
  rm -f "$BOOT_UNIT"; systemctl daemon-reload 2>/dev/null || true
  rm -f "$STATE"
  echo "Reverted to pre-tuning state."
}

case "${1:-}" in
  apply)   cmd_apply ;;
  revert)  cmd_revert ;;
  reapply) [ -f "$STATE" ] && apply_live && echo "re-applied" || echo "inactive" ;;
  status)  [ -f "$STATE" ] && echo active || echo inactive ;;
  *) echo "usage: zeta-tuning {apply|revert|reapply|status}" >&2; exit 1 ;;
esac
