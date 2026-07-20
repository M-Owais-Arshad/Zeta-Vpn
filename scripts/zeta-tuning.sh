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
# Design goals:
#   • Everything changed is snapshotted first, so `revert` puts the box back
#     EXACTLY as it was — sysctls, qdisc, CPU governor, and the systemd cgroup
#     weights (incl. the live /run overrides).
#   • Fully graceful: a sysctl/knob the running kernel doesn't have is silently
#     skipped, never written, never errored. Nothing here can crash the box or
#     break connectivity — every step is best-effort ( || true ) and reversible.
#   • Never opens a port / touches ufw / fail2ban / DNS.
set -uo pipefail

SNAP=/var/backups/zeta-tune
# Loads LAST among /etc/sysctl.d (so it wins over any hand-written 99-zeta*.conf)
SYSCTL_D=/etc/sysctl.d/99-zzz-zeta-gaming.conf
SYSCTL_D_OLD=/etc/sysctl.d/99-zeta-gaming.conf   # pre-rename name, cleaned on revert
BOOT_UNIT=/etc/systemd/system/zeta-gaming-tune.service
STATE="$SNAP/state"

iface() { ip route get 1.1.1.1 2>/dev/null | grep -Po 'dev \K\S+' | head -1; }

# Low-latency tuning set, STABILITY-FIRST (not a max-buffer "beast" profile).
# Tuned for a small (512MB–1GB) VPS running Xray/sing-box: BBR+fq pacing, TFO,
# swappiness low to keep the data plane resident (never 0 — keep an OOM valve).
#
# ONE SOURCE OF TRUTH: the SHARED network knobs (buffer ceilings/floors, tcp_rmem/
# tcp_wmem, somaxconn, notsent_lowat, backlogs) use EXACTLY the values the base
# install already applies in scripts/tune_bbr.sh — the boost never silently
# re-sizes a buffer differently from the baseline. It only ADDS a few gaming-
# specific knobs on top (UDP floors, tw_reuse/fin_timeout, swappiness) plus the
# live non-sysctl bits that actually help under load (fq pacing qdisc, CPU
# governor, cgroup priority). Buffer CEILINGS stay a moderate 16MB (not 64MB) and
# the per-socket UDP floor a gentle 64KB: oversized buffers are the classic cause
# of the "spikes then drops" sawtooth and waste RAM across thousands of flows.
# 16MB saturates any realistic VPS link, butter-smooth.
#
# DELIBERATELY NOT HERE (audited out — hurt more than helped): nf_conntrack
# timeout overrides + rmem_default/wmem_default below stock + a deep txqueuelen +
# a POSTROUTING MSS clamp on the wrong leg. See load_modules()/apply_live().
TUNING_SYSCTLS=(
  "net.ipv4.tcp_congestion_control = bbr"
  "net.core.default_qdisc = fq"
  "net.core.rmem_max = 16777216"
  "net.core.wmem_max = 16777216"
  "net.ipv4.tcp_rmem = 4096 87380 16777216"
  "net.ipv4.tcp_wmem = 4096 65536 16777216"
  "net.ipv4.udp_rmem_min = 65536"
  "net.ipv4.udp_wmem_min = 65536"
  "net.core.netdev_max_backlog = 16384"
  "net.core.somaxconn = 4096"
  "net.ipv4.tcp_max_syn_backlog = 8192"
  "net.ipv4.tcp_fastopen = 3"
  "net.ipv4.tcp_notsent_lowat = 131072"
  "net.ipv4.tcp_mtu_probing = 2"
  "net.ipv4.tcp_base_mss = 1340"
  "net.ipv4.tcp_slow_start_after_idle = 0"
  "net.ipv4.tcp_no_metrics_save = 1"
  "net.ipv4.icmp_ratelimit = 0"
  "net.ipv4.tcp_tw_reuse = 1"
  "net.ipv4.tcp_fin_timeout = 15"
  "vm.swappiness = 10"
)

# Snapshot keys are DERIVED from the set above, so every key we ever set is also
# recorded — no "set-without-snapshot" revert gaps, ever.
SYSCTL_KEYS=()
for _kv in "${TUNING_SYSCTLS[@]}"; do SYSCTL_KEYS+=("${_kv%% *}"); done

# Best-effort load of tcp_bbr (the congestion control we set). Missing = harmless
# skip. (We no longer touch nf_conntrack — its timeout tweaks were removed because
# adding connection tracking to a pure userspace relay can drop packets under
# high churn, which HURT stability under the exact load Boost is meant to help.)
load_modules() {
  modprobe tcp_bbr 2>/dev/null || true
}

snapshot() {
  mkdir -p "$SNAP/dropins"
  load_modules   # so the tcp_bbr key exists and its original gets recorded
  local IF; IF=$(iface); echo "${IF:-eth0}" > "$SNAP/iface.orig"
  # sysctl originals as "key = value" lines so revert is `sysctl -p`.
  : > "$SNAP/sysctl.orig"
  local k v
  for k in "${SYSCTL_KEYS[@]}"; do
    v=$(sysctl -n "$k" 2>/dev/null) || continue   # key absent -> skip gracefully
    printf '%s = %s\n' "$k" "$v" >> "$SNAP/sysctl.orig"
  done
  # Only qdisc is snapshotted here — it's the one live netdev knob Boost changes
  # (root fq). txqueuelen and the mangle MSS rule are no longer touched by Boost,
  # so there's nothing of ours to record/restore for them.
  [ -n "$IF" ] && tc qdisc show dev "$IF" root 2>/dev/null > "$SNAP/qdisc.orig" || true
  { local g
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
      [ -r "$g" ] && echo "$(cat "$g")"
    done
  } > "$SNAP/governor.orig" 2>/dev/null || true
  local svc
  for svc in zeta-panel zeta-xray zeta-singbox zeta-ws; do
    [ -d "/etc/systemd/system/${svc}.service.d" ] && \
      cp -a "/etc/systemd/system/${svc}.service.d" "$SNAP/dropins/${svc}.d" 2>/dev/null || true
  done
}

# Build the persisted sysctl drop-in from ONLY the keys the running kernel
# actually has — an unsupported knob is skipped, so `sysctl -p` never errors and
# boot never warns. (Fully graceful: apply-if-possible, else skip.)
write_sysctl_d() {
  {
    echo "# ZetaVPN elite gaming tuning (managed — removed by 'zeta-tuning revert')"
    local kv key
    for kv in "${TUNING_SYSCTLS[@]}"; do
      key="${kv%% *}"
      sysctl -n "$key" >/dev/null 2>&1 && echo "$kv"
    done
  } > "$SYSCTL_D"
}

# The live, non-sysctl bits (safe to run every boot; idempotent, all best-effort).
apply_live() {
  local IF; IF=$(iface); [ -z "$IF" ] && IF=$(cat "$SNAP/iface.orig" 2>/dev/null)
  load_modules
  # Regenerate the persisted drop-in from the CURRENT tuning set every time before
  # sourcing it. This self-heals a stale file left by an older release on an
  # already-active box (e.g. after 'zeta update' the re-apply / boot-reapply paths
  # would otherwise keep sourcing knobs this version removed). snapshot() has always
  # run before the first apply_live, so originals are already captured.
  write_sysctl_d
  sysctl -p "$SYSCTL_D" >/dev/null 2>&1 || true
  # fq qdisc = BBR's companion pacer (this genuinely helps throughput/latency).
  # We deliberately DON'T bump txqueuelen or add a POSTROUTING MSS clamp here:
  #   - a deep txqueuelen (2000) adds bufferbloat -> ping sawtooth/jitter under load;
  #   - the client-facing MSS clamp already lives in base tune_bbr.sh (--set-mss on
  #     the ingress leg), and a POSTROUTING --clamp-mss-to-pmtu only saw the server's
  #     own 1500 MTU, so it was a no-op on the wrong leg.
  [ -n "$IF" ] && tc qdisc replace dev "$IF" root fq 2>/dev/null || true
  # CPU governor = performance (no-op on KVM guests with no cpufreq)
  local g
  for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [ -w "$g" ] && echo performance > "$g" 2>/dev/null || true
  done
}

write_prio_dropins() {
  local d svc
  # data plane: priority + a high fd ceiling (each proxied flow uses fds on both
  # the inbound and re-originated outbound side; the default 1024/4096 refuses
  # connections under load). LimitNOFILE only bites after the daemon restarts.
  for svc in zeta-xray zeta-singbox zeta-badvpn; do
    d="/etc/systemd/system/${svc}.service.d"; mkdir -p "$d"
    printf '[Service]\nNice=-5\nCPUWeight=300\nIOWeight=200\nLimitNOFILE=1048576\n' > "$d/20-zeta-prio.conf"
  done
  # control plane: deprioritize the panel so it never steals CPU from forwarding
  d="/etc/systemd/system/zeta-panel.service.d"; mkdir -p "$d"
  printf '[Service]\nNice=10\nCPUWeight=50\nIOWeight=50\n' > "$d/20-zeta-deprio.conf"
  systemctl daemon-reload 2>/dev/null || true
  # Apply the cgroup weights to the ALREADY-RUNNING units immediately (a drop-in
  # + daemon-reload only affects the next start; --runtime set-property takes
  # effect live without dropping connections). These land in /run and are undone
  # by remove_prio_dropins() on revert.
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
  # The LIVE weights set via `systemctl set-property --runtime` live in /run and
  # OUTRANK the /etc drop-ins — without removing them, revert would leave xray/
  # sing-box boosted and the panel throttled until the next reboot.
  rm -rf /run/systemd/system.control/zeta-xray.service.d \
         /run/systemd/system.control/zeta-singbox.service.d \
         /run/systemd/system.control/zeta-badvpn.service.d \
         /run/systemd/system.control/zeta-panel.service.d 2>/dev/null || true
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
  load_modules
  [ -f "$STATE" ] && { apply_live; echo "already active (re-applied)"; exit 0; }
  snapshot
  apply_live   # regenerates the sysctl.d drop-in itself (see apply_live)
  write_prio_dropins
  install_boot_unit
  echo active > "$STATE"
  echo "Elite gaming tuning applied."
}

cmd_revert() {
  [ -f "$STATE" ] || { echo "not active"; exit 0; }
  local IF; IF=$(cat "$SNAP/iface.orig" 2>/dev/null); [ -z "$IF" ] && IF=$(iface)
  # sysctls back to snapshot, then drop our files (both current + pre-rename name)
  [ -f "$SNAP/sysctl.orig" ] && sysctl -p "$SNAP/sysctl.orig" >/dev/null 2>&1 || true
  rm -f "$SYSCTL_D" "$SYSCTL_D_OLD"
  if [ -n "$IF" ]; then
    # qdisc back to the original kind (first line only — multi-queue NICs print
    # several); default fq_codel if unknown.
    local kind; kind=$(awk 'NR==1{print $2}' "$SNAP/qdisc.orig" 2>/dev/null); [ -z "$kind" ] && kind=fq_codel
    tc qdisc replace dev "$IF" root "$kind" 2>/dev/null || tc qdisc del dev "$IF" root 2>/dev/null || true
  fi
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
