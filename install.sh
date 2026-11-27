#!/bin/bash
# Install the drain logger and sleep-guard. Run as root (pkexec bash install.sh).
set -euo pipefail
cd "$(dirname "$0")"

install -m 755 sleep-drain-log /usr/lib/systemd/system-sleep/sleep-drain-log
# The power-off after N minutes of sleep on battery: the hook, its switch, and
# the unit that loads the alarm drivers at boot. Off until "sleep-drain-nights on".
install -m 755 sleep-drain-off-after /usr/lib/systemd/system-sleep/sleep-drain-off-after
install -m 755 sleep-drain-nights /usr/local/bin/sleep-drain-nights
install -m 644 sleep-drain-alarm.service /etc/systemd/system/sleep-drain-alarm.service
install -m 755 sleep-guard /usr/local/bin/sleep-guard
install -m 644 sleep-guard.service /etc/systemd/system/sleep-guard.service
# UPower's critical-battery action: power off, not suspend.
install -D -m 644 upower-critical.conf /etc/UPower/UPower.conf.d/90-sleep-drain.conf
install -m 755 sleep-drain-radios /usr/local/bin/sleep-drain-radios
install -m 755 sleep-drain-diff /usr/local/bin/sleep-drain-diff
install -m 755 sleep-drain-wake /usr/local/bin/sleep-drain-wake
install -m 755 sleep-drain-pcie /usr/local/bin/sleep-drain-pcie
install -m 755 sleep-drain-pmp /usr/local/bin/sleep-drain-pmp
install -m 755 test-kernel /usr/local/bin/test-kernel
# The loader swap for a reference build, and the way back from it (pkexec ref-loader restore).
install -m 755 ref-loader /usr/local/bin/ref-loader
install -m 755 sleep-drain-safe /usr/local/bin/sleep-drain-safe
install -m 755 sleep-drain-reading /usr/local/bin/sleep-drain-reading
# The safe boot's initramfs hook. Only sleep-drain-safe's own image has it
# (mkinitcpio -A); the regular initramfs doesn't.
install -D -m 644 safe-boot/sleepdrain-safe.hook /etc/initcpio/hooks/sleepdrain-safe
install -D -m 644 safe-boot/sleepdrain-safe.install /etc/initcpio/install/sleepdrain-safe
install -m 755 pmu-alarm /usr/local/bin/pmu-alarm
cc -O2 -Wall -o /usr/local/bin/pmp-request pmp-request.c
# The pmp_sleep_release experiment module, built for the running kernel in a
# scratch copy so the repo gets no root-owned build files. Installed outside
# /usr/lib/modules, so nothing loads it but sleep-drain-pmp. Not fatal: right
# after a kernel update, the running kernel's headers are gone.
build=$(mktemp -d)
cp pmp-sleep-release/pmp_sleep_release.c pmp-sleep-release/Makefile "$build"/
if make -s -C "$build" > /dev/null; then
  install -D -m 644 "$build/pmp_sleep_release.ko" /usr/local/lib/sleep-drain/pmp_sleep_release.ko
else
  echo "pmp_sleep_release not built for $(uname -r); the pmp experiment is unavailable" >&2
fi
rm -rf -- "${build:?}"
# smc-peek, built the same way, for sleep-drain-reading's BAAC, BACC and BQCC.
# Only sleep-drain-reading loads it.
build=$(mktemp -d)
cp smc-peek/smc_peek.c smc-peek/Makefile "$build"/
if make -s -C "$build" > /dev/null; then
  install -D -m 644 "$build/smc_peek.ko" /usr/local/lib/sleep-drain/smc_peek.ko
else
  echo "smc_peek not built for $(uname -r); readings go without BACC and BQCC" >&2
fi
rm -rf -- "${build:?}"
# The PMU RTC alarm drivers of rtc-alarm-test, built the same way, with a copy
# of load, which finds the modules beside itself. sleep-drain-alarm.service
# runs that copy at boot. After a kernel update these no longer match, load
# refuses them, and sleeps aren't cut short until this script has run again.
alarm_built=no
build=$(mktemp -d)
cp rtc-alarm-test/rtc-macsmc-alarm.c rtc-alarm-test/spmi-apple-wake.c rtc-alarm-test/pmu-alarm-dt.c \
  rtc-alarm-test/Makefile "$build"/
install -D -m 755 rtc-alarm-test/load /usr/local/lib/sleep-drain/rtc-alarm/load
if make -s -C "$build" > /dev/null; then
  for ko in rtc-macsmc-alarm spmi-apple-wake pmu-alarm-dt; do
    install -D -m 644 "$build/$ko.ko" "/usr/local/lib/sleep-drain/rtc-alarm/$ko.ko"
  done
  alarm_built=yes
else
  echo "the alarm drivers not built for $(uname -r); sleeps on battery aren't cut short" >&2
fi
rm -rf -- "${build:?}"
install -d -m 755 /etc/sleep-drain /var/log/sleep-drain
touch /var/log/sleep-drain.log /var/log/sleep-drain-off-after.log
chmod 644 /var/log/sleep-drain.log /var/log/sleep-drain-off-after.log

# Whether the alarm RTC is there, as the sleep-drain-off-after hook finds it.
alarm_rtc_there() {
  local r
  while IFS= read -r r; do
    if [[ $(basename "$(readlink -f "$r/device/driver/module" 2> /dev/null)") == rtc_macsmc_alarm &&
      -e $r/wakealarm ]]; then
      return 0
    fi
  done < <(compgen -G "${OFF_AFTER_RTC_GLOB:-/sys/class/rtc/rtc*}")
  return 1
}

# After a kernel update the enabled sleep-drain-alarm.service has failed at
# boot on the old modules. With the new ones built ($1 is yes), start it now,
# so the alarm is there without a restart. Only if the unit is enabled
# ("sleep-drain-nights on") and the alarm RTC isn't there: load refuses to run
# over drivers that are loaded already. The unit's own conditions keep it off
# in a safe boot and without the flag. Not fatal: the unit says why it failed.
start_alarm_unit() {
  [[ $1 == yes ]] || return 0
  systemctl -q is-enabled sleep-drain-alarm.service 2> /dev/null || return 0
  if alarm_rtc_there; then return 0; fi
  systemctl start sleep-drain-alarm.service ||
    echo "sleep-drain-alarm.service didn't start (systemctl status sleep-drain-alarm.service); sleeps on battery aren't cut short" >&2
}

systemctl daemon-reload
start_alarm_unit "$alarm_built"
systemctl enable sleep-guard.service
systemctl restart sleep-guard.service
# UPower reads its configuration only when it starts.
systemctl try-restart upower.service
echo "installed"
