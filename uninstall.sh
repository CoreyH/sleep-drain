#!/bin/bash
# Remove the drain logger and sleep-guard. Keeps /var/log/sleep-drain.log.
set -uo pipefail

systemctl disable --now sleep-guard.service
rmmod pmp_sleep_release 2> /dev/null
rm -rf /usr/local/lib/sleep-drain
rm -f /etc/systemd/system/sleep-guard.service /usr/local/bin/sleep-guard \
  /usr/lib/systemd/system-sleep/sleep-drain-log /run/sleep-drain.pre \
  /usr/local/bin/sleep-drain-radios /usr/local/bin/sleep-drain-diff \
  /usr/local/bin/sleep-drain-wake /usr/local/bin/sleep-drain-pcie \
  /usr/local/bin/sleep-drain-pmp /usr/local/bin/pmp-request /usr/local/bin/test-kernel
rm -rf /etc/sleep-drain
systemctl daemon-reload
echo "removed (log kept at /var/log/sleep-drain.log, snapshots in /var/log/sleep-drain/)"
