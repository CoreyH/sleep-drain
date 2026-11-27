#!/bin/bash
# Remove the drain logger and sleep-guard. Keeps /var/log/sleep-drain.log.
set -uo pipefail

systemctl disable --now sleep-guard.service
# The alarm drivers, if loaded, stay until the next restart.
systemctl disable sleep-drain-alarm.service
rm -f /etc/systemd/system/sleep-drain-alarm.service /usr/local/bin/sleep-drain-nights \
  /usr/lib/systemd/system-sleep/sleep-drain-off-after /run/sleep-drain-off-after
[[ -x /usr/local/bin/sleep-drain-safe ]] && /usr/local/bin/sleep-drain-safe remove
rm -f /etc/initcpio/hooks/sleepdrain-safe /etc/initcpio/install/sleepdrain-safe
rmmod pmp_sleep_release 2> /dev/null
rm -rf /usr/local/lib/sleep-drain
rm -f /etc/systemd/system/sleep-guard.service /usr/local/bin/sleep-guard \
  /usr/lib/systemd/system-sleep/sleep-drain-log /run/sleep-drain.pre \
  /usr/local/bin/sleep-drain-radios /usr/local/bin/sleep-drain-diff \
  /usr/local/bin/sleep-drain-wake /usr/local/bin/sleep-drain-pcie \
  /usr/local/bin/sleep-drain-pmp /usr/local/bin/pmp-request /usr/local/bin/test-kernel \
  /usr/local/bin/pmu-alarm /usr/local/bin/sleep-drain-safe
rm -rf /etc/sleep-drain
# ref-loader stays while a stock loader is kept or recorded: it is the way back from Linux to the stock boot.bin.
if [[ -e /boot/m1n1/boot.bin.stock || -e /var/lib/sleep-drain/ref-loader/stock.sha256 ]]; then
  echo "kept /usr/local/bin/ref-loader: a stock loader is kept for it (ref-loader status). Remove it by hand once boot.bin is the stock one"
else
  rm -f /usr/local/bin/ref-loader
fi
systemctl daemon-reload
echo "removed (log kept at /var/log/sleep-drain.log, snapshots in /var/log/sleep-drain/)"
