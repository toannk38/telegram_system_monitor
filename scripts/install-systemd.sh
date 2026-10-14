#!/usr/bin/env bash
# Install / update / remove the System Monitor bot as a systemd service.
#
#   ./scripts/install-systemd.sh               # install or update (runs as current user)
#   RUN_USER=monitor ./scripts/install-systemd.sh
#   ./scripts/install-systemd.sh --uninstall
#
# Run as a normal user; sudo is used only for the systemd steps.
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-system-monitor}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/deploy/systemd/system-monitor.service.template"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
RUN_GROUP="${RUN_GROUP:-$(id -gn "$RUN_USER")}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

as_run_user() {
    if [[ "$(id -un)" == "$RUN_USER" ]]; then
        "$@"
    else
        sudo -u "$RUN_USER" -H "$@"
    fi
}

uninstall() {
    log "Stopping and removing $SERVICE_NAME"
    sudo systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$UNIT_PATH"
    sudo systemctl daemon-reload
    log "Removed. Logs/data in $PROJECT_DIR are kept."
}

install() {
    id "$RUN_USER" >/dev/null 2>&1 || die "User '$RUN_USER' does not exist"
    [[ -f "$PROJECT_DIR/.env" ]] || die "Missing $PROJECT_DIR/.env — copy .env.example and fill BOT_TOKEN first"

    local uv_bin
    uv_bin="$(as_run_user bash -lc 'command -v uv' || true)"
    [[ -n "$uv_bin" ]] || die "uv not found for user $RUN_USER — install: curl -LsSf https://astral.sh/uv/install.sh | sh"

    log "Syncing dependencies with uv ($uv_bin) as $RUN_USER"
    (cd "$PROJECT_DIR" && as_run_user "$uv_bin" sync --frozen --no-dev)

    as_run_user mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"

    log "Writing $UNIT_PATH (user=$RUN_USER, dir=$PROJECT_DIR)"
    sed -e "s|@RUN_USER@|$RUN_USER|g" \
        -e "s|@RUN_GROUP@|$RUN_GROUP|g" \
        -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
        "$TEMPLATE" | sudo tee "$UNIT_PATH" >/dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME" >/dev/null
    sudo systemctl restart "$SERVICE_NAME"

    sleep 2
    sudo systemctl --no-pager --lines=10 status "$SERVICE_NAME" || true
    log "Done. Follow logs: journalctl -u $SERVICE_NAME -f"
}

case "${1:-}" in
    --uninstall) uninstall ;;
    ""|--install) install ;;
    *) die "Unknown option: $1 (use --install or --uninstall)" ;;
esac
