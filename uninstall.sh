#!/bin/bash
# Remove the drain logger and sleep-guard. Keeps /var/log/sleep-drain.log.
set -uo pipefail

systemctl disable --now sleep-guard.service
rm -f /etc/systemd/system/sleep-guard.service /usr/local/bin/sleep-guard \
  /usr/lib/systemd/system-sleep/sleep-drain-log /run/sleep-drain.pre \
  /usr/local/bin/sleep-drain-radios /usr/local/bin/sleep-drain-diff
rm -rf /etc/sleep-drain
systemctl daemon-reload
echo "removed (log kept at /var/log/sleep-drain.log, snapshots in /var/log/sleep-drain/)"
