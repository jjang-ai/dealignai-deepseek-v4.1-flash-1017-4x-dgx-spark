#!/bin/bash
# ring0.sh <rank>: node IP 10.10.100.<rank+1>/32 on dummy ring0, routed over the CX7 ring (opposite via clockwise neighbour)
set -e; r=$1; n=$(( (r+1)%4 )); p=$(( (r+3)%4 )); o=$(( (r+2)%4 ))
nmcli con delete ring0 >/dev/null 2>&1 || true
nmcli con add type dummy ifname ring0 con-name ring0 ipv4.method manual ipv4.addresses 10.10.100.$((r+1))/32 ipv6.method disabled connection.autoconnect yes >/dev/null
# src= pinned to the node IP: without it the kernel sources from the link address and the opposite node
# (reached through the clockwise neighbour) has no route back, so TCP to it hangs while ping -I works.
s=10.10.100.$((r+1))
nmcli con mod ring-enp1s0f0np0 ipv4.routes "10.10.100.$((n+1))/32 10.10.$((2*r)).2 src=$s, 10.10.100.$((o+1))/32 10.10.$((2*r)).2 src=$s"
nmcli con mod ring-enp1s0f1np1 ipv4.routes "10.10.100.$((p+1))/32 10.10.$((2*p)).1 src=$s"
nmcli con up ring0 >/dev/null; nmcli con up ring-enp1s0f0np0 >/dev/null; nmcli con up ring-enp1s0f1np1 >/dev/null
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/61-ring-forward.conf; sysctl -q -p /etc/sysctl.d/61-ring-forward.conf
cat > /etc/systemd/system/ring-forward.service <<U
[Unit]
Description=Accept forwarded traffic between CX7 ring ports (DOCKER-USER)
After=docker.service network-online.target
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'for a in enp1s0f0np0 enp1s0f1np1; do for b in enp1s0f0np0 enp1s0f1np1; do iptables -C DOCKER-USER -i \$a -o \$b -j ACCEPT 2>/dev/null || iptables -I DOCKER-USER -i \$a -o \$b -j ACCEPT; iptables -C FORWARD -i \$a -o \$b -j ACCEPT 2>/dev/null || iptables -I FORWARD -i \$a -o \$b -j ACCEPT; done; done'
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload; systemctl enable --now ring-forward.service >/dev/null 2>&1; systemctl restart ring-forward.service
ip -4 route | grep 10.10.100
