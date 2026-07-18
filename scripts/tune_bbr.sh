#!/usr/bin/env bash
# Stability-first network tuning that runs on ANY VPS without ever failing.
#
# Design goal: the "best stable speed this particular kernel/host can give",
# applied SAFELY everywhere — bare-metal, KVM, LXC, and even locked-down
# OpenVZ where most sysctls are read-only. Every knob is applied live, verified,
# and skipped cleanly if the kernel doesn't expose it or refuses the value; only
# the knobs that actually stuck are persisted, so a reboot re-applies exactly
# what works on this box (and systemd-sysctl never errors on an unsupported key).
#
# NOTE: `set -e` is deliberately OMITTED — a single unsupported sysctl on some
# provider must never abort the tuning (or the install).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${HERE}/common.sh"
need_root

msg "Applying stability-first network tuning (self-adapting, never-fail)"

PERSIST=/etc/sysctl.d/99-zeta.conf
TMP="$(mktemp)"

# write_sysctl <key> <value>
# Apply live, VERIFY it took (some namespaced keys silently ignore writes), and
# only then record it for persistence. Returns non-zero (skips) if the key is
# absent or read-only, so callers can fall back to a second-best value.
write_sysctl() {
  local key="$1" val="$2" path="/proc/sys/${1//.//}" got exp
  [ -e "$path" ] || return 1                       # key not present on this kernel
  sysctl -w "${key}=${val}" >/dev/null 2>&1 || return 1
  got="$(sysctl -n "$key" 2>/dev/null | tr -d '[:space:]')"
  exp="$(printf '%s' "$val" | tr -d '[:space:]')"
  [ "$got" = "$exp" ] || return 1                  # write silently ignored (container)
  printf '%s = %s\n' "$key" "$val" >> "$TMP"
  return 0
}

# --- Congestion control: BBR if this kernel has it, else keep the default ----
# BBR is the single biggest stability+throughput win for a proxy (great on
# lossy / high-RTT paths). If unavailable (old/stripped kernel), we leave the
# kernel default (usually cubic) — still perfectly stable, just less optimal.
modprobe tcp_bbr 2>/dev/null || true
if grep -qw bbr /proc/sys/net/ipv4/tcp_available_congestion_control 2>/dev/null; then
  write_sysctl net.ipv4.tcp_congestion_control bbr || true
fi

# --- qdisc: fq (BBR's pacing partner) > fq_codel > leave default -------------
# net.core.default_qdisc only applies to interfaces brought up AFTER it's set, so
# without a live swap the already-up NIC keeps the distro-default qdisc until the
# next reboot — base BBR then runs without its fq pacing partner. Apply it live to
# the primary interface too (best-effort; harmless if tc/iface is absent).
if write_sysctl net.core.default_qdisc fq || write_sysctl net.core.default_qdisc fq_codel; then
  QDISC="$(sysctl -n net.core.default_qdisc 2>/dev/null || echo fq)"
  IFACE="$(ip -o route show default 2>/dev/null | awk '{print $5; exit}')"
  [ -n "$IFACE" ] && tc qdisc replace dev "$IFACE" root "$QDISC" 2>/dev/null || true
fi

# --- Buffers: a 16MB stability-first sweet spot ------------------------------
# Moderate ceilings: big enough to saturate any realistic VPS link, small
# enough to never bufferbloat (the classic cause of the speed sawtooth).
write_sysctl net.core.rmem_max 16777216 || true
write_sysctl net.core.wmem_max 16777216 || true
write_sysctl net.ipv4.tcp_rmem "4096 87380 16777216" || true
write_sysctl net.ipv4.tcp_wmem "4096 65536 16777216" || true

# --- Safe, universal latency/throughput helpers ------------------------------
write_sysctl net.ipv4.tcp_fastopen 3 || true
write_sysctl net.ipv4.tcp_slow_start_after_idle 0 || true   # keep speed up after idle
write_sysctl net.ipv4.tcp_notsent_lowat 131072 || true      # trim local send bufferbloat
# mtu_probing=2 + a low base_mss makes new connections start SMALL (1340) and
# probe UP, instead of =1 which only reacts AFTER a large packet already stalled
# (the "new sites hang over a tunnel" symptom). Proactive beats reactive here.
write_sysctl net.ipv4.tcp_mtu_probing 2 || true
write_sysctl net.ipv4.tcp_base_mss 1340 || true
write_sysctl net.core.somaxconn 4096 || true
write_sysctl net.ipv4.tcp_max_syn_backlog 8192 || true
write_sysctl net.core.netdev_max_backlog 16384 || true
write_sysctl net.ipv4.ip_forward 1 || true
write_sysctl fs.file-max 1000000 || true
# Per-socket UDP buffer floor so QUIC cores (Hysteria2/TUIC via quic-go) don't
# drop under memory pressure on a non-boosted box (was Boost-only).
write_sysctl net.ipv4.udp_rmem_min 65536 || true
write_sysctl net.ipv4.udp_wmem_min 65536 || true

# Persist ONLY the keys that actually stuck on this box (atomic replace), so the
# reboot re-apply is clean and never logs a "cannot set" error.
if [ -s "$TMP" ]; then
  { echo "# Managed by ZetaVPN — stability-first, self-adapted to this kernel"; cat "$TMP"; } > "$PERSIST"
else
  # Nothing was settable (extreme locked-down container) — don't leave a stale file.
  rm -f "$PERSIST"
fi
rm -f "$TMP"

# --- Client-facing TCP MSS clamp: the definitive server-side fix for the
# "new sites hang over a tunnel" PMTU blackhole affecting clients that CANNOT
# set their own MTU (OpenTunnel over SSH/WS/stunnel, HTTP Injector). The box is
# a TERMINATING userspace proxy (nginx/xray/ws-proxy all bind loopback), so the
# client<->server TCP is LOCAL (PREROUTING->INPUT, never FORWARD). Rewriting the
# client SYN's advertised MSS on PREROUTING caps every server->client download
# segment, so the origin's relayed TLS ServerHello/cert fits the constrained
# CGNAT/DPI client path instead of being silently dropped. --set-mss (fixed),
# NOT --clamp-mss-to-pmtu: the server's own PMTU to its first hop reads 1500 and
# can't see the middle hop. Loopback excluded so internal hops keep full
# segments. Ship a tiny script + boot oneshot; the -C guard makes every apply
# idempotent (re-running on `zeta update` or boot never stacks duplicates).
# (TCP only — QUIC/Hysteria2 has no server MTU knob; those clients set MTU 1380.)
ZETA_MSS="${ZETA_MSS:-1360}"
cat > /usr/local/sbin/zeta-mss-clamp <<CLAMP
#!/usr/bin/env bash
# Managed by ZetaVPN (tune_bbr.sh) — idempotent client-facing MSS clamp.
M=${ZETA_MSS}
for spec in "PREROUTING ! -i lo" "OUTPUT ! -o lo"; do
  read -r chain flag dev <<<"\$spec"
  iptables -t mangle -C \$chain \$flag \$dev -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "\$M" 2>/dev/null \
    || iptables -t mangle -A \$chain \$flag \$dev -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "\$M" 2>/dev/null || true
done
CLAMP
chmod 0755 /usr/local/sbin/zeta-mss-clamp
/usr/local/sbin/zeta-mss-clamp   # apply live now
cat > /etc/systemd/system/zeta-mss.service <<'UNIT'
[Unit]
Description=ZetaVPN client-facing TCP MSS clamp (tunnel MTU fix)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/zeta-mss-clamp
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload 2>/dev/null || true
systemctl enable zeta-mss.service >/dev/null 2>&1 || true
ok "Client-facing MSS clamp active (--set-mss ${ZETA_MSS}, tunnel MTU fix)"

CC="$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || echo unknown)"
QD="$(sysctl -n net.core.default_qdisc 2>/dev/null || echo unknown)"
if [ "$CC" = bbr ]; then
  ok "BBR active (cc=bbr, qdisc=${QD}) — best stable profile"
else
  warn "BBR unavailable on this kernel; using cc=${CC}, qdisc=${QD} (stable, just less optimal). A reboot may enable BBR on some hosts."
fi

# Raise open-file limits for the proxy cores (best-effort).
if ! grep -q 'ZetaVPN limits' /etc/security/limits.conf 2>/dev/null; then
  cat >> /etc/security/limits.conf <<'CONF'
# ZetaVPN limits
* soft nofile 1000000
* hard nofile 1000000
CONF
fi
ok "Network tuning applied"
