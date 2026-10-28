#!/bin/bash
# Install the drain logger and sleep-guard. Run as root (pkexec bash install.sh).
set -euo pipefail
cd "$(dirname "$0")"

install -m 755 sleep-drain-log /usr/lib/systemd/system-sleep/sleep-drain-log
install -m 755 sleep-guard /usr/local/bin/sleep-guard
install -m 644 sleep-guard.service /etc/systemd/system/sleep-guard.service
install -m 755 sleep-drain-radios /usr/local/bin/sleep-drain-radios
install -m 755 sleep-drain-diff /usr/local/bin/sleep-drain-diff
install -m 755 sleep-drain-wake /usr/local/bin/sleep-drain-wake
install -m 755 sleep-drain-pcie /usr/local/bin/sleep-drain-pcie
install -d -m 755 /etc/sleep-drain /var/log/sleep-drain
touch /var/log/sleep-drain.log
chmod 644 /var/log/sleep-drain.log

systemctl daemon-reload
systemctl enable sleep-guard.service
systemctl restart sleep-guard.service
echo "installed"
