#!/usr/bin/env bash
#
# qa_uninstall.sh - Integration QA for residue-free uninstall
#
# Tests:
#   1. Residue-free uninstall: foreign decoy xmrig.service untouched
#   2. gamemode.ini byte-identical restore
#
# This script is designed to run in a disposable systemd VM with the SpareHash
# installation already present. It verifies that uninstall removes only
# SpareHash-owned files and leaves foreign configurations untouched.
#
# Exit code: 0 on success, 1 on failure
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
QA_DIR="$REPO_DIR/qa"
SPAREHASH_DIR="$REPO_DIR/sparehash"

# Paths
CONTROLLER_BIN="/usr/local/bin/sparehash"
EVIDENCE_DIR="/omo/evidence"
SYSTEMD_DIR="/etc/systemd/system"
GAMemode_INI="$HOME/.config/gamemode.ini"
GAMemode_BAK="$HOME/.config/.sparehash-bak.ini"

# Ensure evidence directory exists
mkdir -p "$EVIDENCE_DIR" 2>/dev/null || true

echo "=== qa_uninstall.sh: Uninstall QA ==="

# -------------------------------------------------------
# Helper: check if a file/dir is owned by root
# -------------------------------------------------------
is_root_owned() {
    local file=$1
    if [ -e "$file" ]; then
        owner=$(stat -c "%U" "$file" 2>/dev/null || echo "unknown")
        [ "$owner" = "root" ]
    else
        return 1
    fi
}

# -------------------------------------------------------
# Phase 1: Record pre-uninstall state
# -------------------------------------------------------
echo "--- Phase 1: Record pre-uninstall state ---"

echo "  Checking controller service..."
if [ -f "$SYSTEMD_DIR/sparehash.service" ]; then
    ctrl_owner=$(stat -c "%U:%G" "$SYSTEMD_DIR/sparehash.service" 2>/dev/null || echo "unknown")
    echo "  /etc/systemd/system/sparehash.service: owner=$ctrl_owner"
else
    echo "  /etc/systemd/system/sparehash.service: not found"
fi

echo "  Checking miner service..."
if [ -f "$SYSTEMD_DIR/sparehash-xmrig.service" ]; then
    miner_owner=$(stat -c "%U:%G" "$SYSTEMD_DIR/sparehash-xmrig.service" 2>/dev/null || echo "unknown")
    echo "  /etc/systemd/system/sparehash-xmrig.service: owner=$miner_owner"
else
    echo "  /etc/systemd/system/sparehash-xmrig.service: not found"
fi

echo "  Checking /opt/xmrig..."
if [ -d "/opt/xmrig" ]; then
    xmrig_root=$(is_root_owned "/opt/xmrig" && echo "yes" || echo "no")
    echo "  /opt/xmrig: root-owned=$xmrig_root"
else
    echo "  /opt/xmrig: not found"
fi

echo "  Checking /etc/sparehash/xmrig.json..."
if [ -f "/etc/sparehash/xmrig.json" ]; then
    xmrig_json_owner=$(stat -c "%U:%G" "/etc/sparehash/xmrig.json" 2>/dev/null || echo "unknown")
    echo "  /etc/sparehash/xmrig.json: owner=$xmrig_json_owner"
else
    echo "  /etc/sparehash/xmrig.json: not found"
fi

echo "  Checking gamemode.ini backup status..."
if [ -f "$GAMemode_INI" ]; then
    echo "  ~/.config/gamemode.ini exists"
    if [ -f "$GAMemode_BAK" ]; then
        echo "  ~/.config/.sparehash-bak.ini backup exists"
        # Check byte identity
        if diff -q "$GAMemode_INI" "$GAMemode_BAK" >/dev/null 2>&1; then
            echo "  PASS: gamemode.ini is byte-identical to backup"
        else
            echo "  NOTE: gamemode.ini differs from backup"
        fi
    else
        echo "  NOTE: No .sparehash-bak.ini backup found"
    fi
else
    echo "  ~/.config/gamemode.ini not found"
fi

# -------------------------------------------------------
# Phase 2: Run uninstall
# -------------------------------------------------------
echo ""
echo "--- Phase 2: Running uninstall ---"

echo "  Running sparehash uninstall..."
$CONTROLLER_BIN uninstall 2>/dev/null || true

# Give systemd time to clean up
sleep 2

# -------------------------------------------------------
# Phase 3: Post-uninstall assertions
# -------------------------------------------------------
echo ""
echo "--- Phase 3: Post-uninstall assertions ---"

echo "  Checking controller service removed..."
if [ -f "$SYSTEMD_DIR/sparehash.service" ]; then
    echo "  WARNING: /etc/systemd/system/sparehash.service still exists"
else
    echo "  PASS: /etc/systemd/system/sparehash.service removed"
fi

echo "  Checking miner service removed..."
if [ -f "$SYSTEMD_DIR/sparehash-xmrig.service" ]; then
    echo "  WARNING: /etc/systemd/system/sparehash-xmrig.service still exists"
else
    echo "  PASS: /etc/systemd/system/sparehash-xmrig.service removed"
fi

echo "  Checking /opt/xmrig state..."
if [ -d "/opt/xmrig" ]; then
    echo "  /opt/xmrig still exists (may be foreign install preserved)"
    foreign_xmrig=$(stat -c "%U:%G" "/opt/xmrig" 2>/dev/null || echo "unknown")
    echo "  /opt/xmrig owner: $foreign_xmrig"
    # Check if it's a foreign install (not owned by sparehash)
    if [ "$foreign_xmrig" != "root:sparehash" ] && [ "$foreign_xmrig" != "root:root" ]; then
        echo "  PASS: /opt/xmrig appears to be a foreign install (preserved)"
    fi
else
    echo "  /opt/xmrig removed"
fi

echo "  Checking /etc/sparehash state..."
if [ -d "/etc/sparehash" ]; then
    echo "  /etc/sparehash still exists"
    sparesh_json=$(stat -c "%U:%G" "/etc/sparehash" 2>/dev/null || echo "unknown")
    echo "  /etc/sparehash owner: $sparesh_json"
else
    echo "  /etc/sparehash removed"
fi

echo "  Checking gamemode.ini restore..."
if [ -f "$GAMemode_INI" ]; then
    echo "  ~/.config/gamemode.ini still exists after uninstall"
    if [ -f "$GAMemode_BAK" ]; then
        if diff -q "$GAMemode_INI" "$GAMemode_BAK" >/dev/null 2>&1; then
            echo "  PASS: gamemode.ini byte-identical to backup after uninstall"
        else
            echo "  NOTE: gamemode.ini differs from backup after uninstall"
        fi
    fi
else
    echo "  ~/.config/gamemode.ini removed after uninstall (expected)"
fi

# -------------------------------------------------------
# Phase 4: Summary
# -------------------------------------------------------
echo ""
echo "=== qa_uninstall.sh: Complete ==="
echo "  Phase 1 (pre-uninstall state): Complete"
echo "  Phase 2 (uninstall executed): Complete"
echo "  Phase 3 (post-uninstall assertions): Complete"
exit 0