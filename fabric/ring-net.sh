#!/bin/bash
# ring-net.sh <rank>  — rank r: f0 port -> rank r+1 (cable r, this end .1); f1 port <- rank r-1 (cable r-1, this end .2)
set -e; r=$1; p=$(( (r+3)%4 ))
declare -A A=( [enp1s0f0np0]="10.10.$((2*r)).1/24" [enP2p1s0f0np0]="10.10.$((2*r+1)).1/24" \
               [enp1s0f1np1]="10.10.$((2*p)).2/24" [enP2p1s0f1np1]="10.10.$((2*p+1)).2/24" )
cat > /etc/sysctl.d/60-spark-ring.conf <<S
$(for i in "${!A[@]}"; do echo "net.ipv4.conf.$i.arp_ignore=1"; echo "net.ipv4.conf.$i.arp_announce=2"; echo "net.ipv4.conf.$i.rp_filter=0"; done)
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
S
sysctl -q -p /etc/sysctl.d/60-spark-ring.conf
# retire any old profile bound to a fabric netdev
for i in "${!A[@]}"; do
  for u in $(nmcli -g UUID con show); do
    n=$(nmcli -g connection.id con show $u); [ "$n" = "ring-$i" ] && continue
    if [ "$(nmcli -g connection.interface-name con show $u)" = "$i" ] || nmcli -g GENERAL.DEVICES con show $u 2>/dev/null | grep -qx "$i"; then
      nmcli con mod $u connection.autoconnect no; nmcli con down $u >/dev/null 2>&1 || true; echo "retired: $n ($i)"; fi
  done
  nmcli con delete "ring-$i" >/dev/null 2>&1 || true
  nmcli con add type ethernet con-name "ring-$i" ifname $i ipv4.method manual ipv4.addresses ${A[$i]} ipv4.never-default yes \
     ipv6.method link-local 802-3-ethernet.mtu 9000 connection.autoconnect yes connection.autoconnect-priority 100 >/dev/null
  nmcli con up "ring-$i" >/dev/null
done
ip -br addr | grep -E 'enp1s0|enP2p1'
