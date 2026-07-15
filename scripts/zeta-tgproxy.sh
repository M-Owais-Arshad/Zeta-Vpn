#!/usr/bin/env bash
# ZetaVPN — Telegram MTProto proxy (mtg v2) build / teardown / status.
# ZetaVPN by Muhammad Owais · (c) 2026 · AGPL-3.0
#
# Usage (root only; the panel reaches it through zeta-privileged):
#   zeta-tgproxy start   — install mtg if needed, generate a FakeTLS secret,
#                          write config, open the firewall, run the service,
#                          and write the client link to /etc/mtg/link
#   zeta-tgproxy stop    — stop+disable the service, close the firewall port,
#                          wipe the live secret
#   zeta-tgproxy status  — print "active"/"inactive" and the link if active
#
# mtg is a single static Go binary (github.com/9seconds/mtg), shipped exactly
# like xray/sing-box: download -> verify SHA256 or refuse -> install to
# /usr/local/bin. FakeTLS camouflages the proxy as TLS to a real domain.
set -uo pipefail

PORT="${ZETA_MTPROXY_PORT:-8443}"
DOMAIN="${ZETA_MTPROXY_DOMAIN:-www.cloudflare.com}"
MTG_BIN=/usr/local/bin/mtg
CFG_DIR=/etc/mtg
CFG="$CFG_DIR/config.toml"
LINK="$CFG_DIR/link"

# Validate the (env-supplied) port/domain inside the root script itself — never
# trust the caller, even though sudo's env_reset currently blocks these vars.
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] \
  || { echo "zeta-tgproxy: invalid port: $PORT" >&2; exit 1; }
[[ "$DOMAIN" =~ ^[a-zA-Z0-9.-]{1,253}$ ]] \
  || { echo "zeta-tgproxy: invalid domain: $DOMAIN" >&2; exit 1; }

# The service runs as zetavpn (falls back to root in dev). Config lives in a
# ROOT-owned dir that zetavpn can only READ (group + mode) — never own/write —
# so a compromised panel user can't plant a symlink for root to follow.
SVC_GRP=$(id zetavpn >/dev/null 2>&1 && echo zetavpn || echo root)
UNIT=/etc/systemd/system/zeta-mtproxy.service
MTG_VERSION="2.2.8"

die() { echo "zeta-tgproxy: $*" >&2; exit 1; }

install_mtg() {
  [ -x "$MTG_BIN" ] && return 0
  local arch tarball url base tmp
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) die "unsupported arch: $(uname -m)" ;;
  esac
  base="https://github.com/9seconds/mtg/releases/download/v${MTG_VERSION}"
  tarball="mtg-${MTG_VERSION}-linux-${arch}.tar.gz"
  tmp=$(mktemp -d)
  echo "[tgproxy] downloading mtg ${MTG_VERSION} (${arch})..."
  curl -fsSL "${base}/${tarball}" -o "$tmp/mtg.tar.gz" || { rm -rf "$tmp"; die "download failed"; }
  # MANDATORY SHA256 verification (goreleaser publishes checksums.txt) — a
  # missing checksums file or an unmatched line is a HARD failure, never a
  # silent skip: this binary runs as a public network-facing service.
  local want have
  curl -fsSL "${base}/mtg-${MTG_VERSION}-checksums.txt" -o "$tmp/checksums.txt" || { rm -rf "$tmp"; die "cannot fetch checksums — refusing to install unverified"; }
  want=$(grep " ${tarball}\$" "$tmp/checksums.txt" | awk '{print $1}')
  have=$(sha256sum "$tmp/mtg.tar.gz" | awk '{print $1}')
  [ -n "$want" ] || { rm -rf "$tmp"; die "no checksum entry for ${tarball} — refusing"; }
  [ "$want" = "$have" ] || { rm -rf "$tmp"; die "checksum mismatch — refusing"; }
  tar -xzf "$tmp/mtg.tar.gz" -C "$tmp"
  install -m 0755 "$(find "$tmp" -name mtg -type f | head -1)" "$MTG_BIN"
  rm -rf "$tmp"
  [ -x "$MTG_BIN" ] || die "mtg install failed"
}

cmd_start() {
  install_mtg
  # Root-owned dir, group-readable by the service user only — NOT owned/writable
  # by zetavpn (prevents a symlink-plant escalation when root writes below).
  install -d -o root -g "$SVC_GRP" -m 0750 "$CFG_DIR"
  local secret
  if [ -s "$CFG" ] && grep -q '^secret' "$CFG"; then
    secret=$(grep '^secret' "$CFG" | head -1 | sed 's/.*"\(.*\)".*/\1/')
  else
    secret=$("$MTG_BIN" generate-secret --hex "$DOMAIN") || die "secret generation failed"
  fi
  # low concurrency — this is a 1GB box (see the panel's low-resource tuning)
  # Public IP for FakeTLS/SNI — EC2 & most VPS sit behind NAT, so mtg can't
  # auto-detect it (doctor's SNI check errors without it). Best-effort; omitted
  # gracefully if detection fails so the build never breaks over it.
  local pubip
  pubip=$(curl -fsS --max-time 6 https://api.ipify.org 2>/dev/null \
          || curl -fsS --max-time 6 https://ifconfig.me 2>/dev/null || true)
  {
    printf 'secret = "%s"\n'          "$secret"
    printf 'bind-to = "0.0.0.0:%s"\n' "$PORT"
    printf 'concurrency = 2048\n'
    [[ "$pubip" =~ ^[0-9.]+$ ]] && printf 'public-ipv4 = "%s"\n' "$pubip"
  } > "$CFG"
  chown root:"$SVC_GRP" "$CFG"; chmod 640 "$CFG"   # file-level read for the service, no dir ownership
  # doctor is ADVISORY only: its SNI / Telegram-DC connectivity checks can flap
  # and do NOT stop the proxy from serving — the real gate is the service
  # actually starting below (systemctl enable --now ... || die).
  "$MTG_BIN" doctor "$CFG" >/dev/null 2>&1 || true

  cat > "$UNIT" <<UNIT
[Unit]
Description=ZetaVPN Telegram MTProto proxy (mtg)
Documentation=https://github.com/9seconds/mtg
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id zetavpn >/dev/null 2>&1 && echo zetavpn || echo root)
WorkingDirectory=${CFG_DIR}
ExecStartPre=-${MTG_BIN} doctor ${CFG}
ExecStart=${MTG_BIN} run ${CFG}
Restart=on-failure
RestartSec=3
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  ufw allow "${PORT}/tcp" >/dev/null 2>&1 || true
  systemctl enable --now zeta-mtproxy >/dev/null 2>&1 || die "service failed to start"
  # Persist the connection parts for the panel to build the link (the panel
  # supplies the public host — mtg only sees EC2's private interface IP).
  { echo "secret=${secret}"; echo "port=${PORT}"; echo "domain=${DOMAIN}"; } > "$LINK"
  chown root:"$SVC_GRP" "$LINK"; chmod 640 "$LINK"
  echo "active"
}

cmd_stop() {
  systemctl disable --now zeta-mtproxy >/dev/null 2>&1 || true
  ufw --force delete allow "${PORT}/tcp" >/dev/null 2>&1 || true
  : > "$CFG" 2>/dev/null || true   # wipe the live secret
  rm -f "$LINK" "$UNIT"
  systemctl daemon-reload 2>/dev/null || true
  echo "inactive"
}

cmd_status() {
  if systemctl is-active --quiet zeta-mtproxy 2>/dev/null; then
    echo active
    [ -f "$LINK" ] && cat "$LINK"
  else
    echo inactive
  fi
}

case "${1:-}" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  *) echo "usage: zeta-tgproxy {start|stop|status}" >&2; exit 1 ;;
esac
