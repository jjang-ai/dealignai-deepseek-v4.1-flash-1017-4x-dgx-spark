#!/usr/bin/env python3
"""dsv41-blackbox — 1 s node health samples, fsync'd so the last seconds before a crash survive it.

One line per second in /var/log/dsv41-blackbox/YYYYMMDD.log (files older than 7 days are removed):
  time memavail_gib swapfree psi_mem_some10 psi_mem_full10 psi_cpu_some10 load1 cpu_temp_max
  gpu_temp gpu_power gpu_sm_clock gpu_throttle cx7_carrier(4 ports) | new Xid / NVRM lines from kmsg
GPU values come from one long-lived `nvidia-smi -lms 1000` process, so the cost is ~nil.
"""
import glob, os, subprocess, threading, time

D = "/var/log/dsv41-blackbox"; os.makedirs(D, exist_ok=True)
gpu = ["-"] * 4
def gpu_reader():
    q = "temperature.gpu,power.draw,clocks.sm,clocks_event_reasons.active"
    while True:
        try:
            p = subprocess.Popen(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits", "-lms", "1000"],
                                 stdout=subprocess.PIPE, text=True)
            for line in p.stdout:
                gpu[:] = [x.strip() for x in line.split(",")][:4]
        except Exception:
            pass
        time.sleep(5)

events = []
def kmsg_reader():
    try:
        with open("/dev/kmsg") as f:
            f.seek(0, os.SEEK_END)
            for line in f:
                l = line.split(";", 1)[-1].strip()
                if any(k in l for k in ("Xid", "NVRM", "mlx5", "thermal", "Hardware Error", "watchdog", "Out of memory")):
                    events.append(l[:300])
    except Exception:
        pass

def read(p, default="-"):
    try:
        with open(p) as f: return f.read()
    except Exception: return default

def psi(kind):
    s = read(f"/proc/pressure/{kind}", "")
    out = {}
    for line in s.splitlines():
        parts = line.split(); out[parts[0]] = parts[1].split("=")[1]
    return out.get("some", "-"), out.get("full", "-")

THERMAL = glob.glob("/sys/class/thermal/thermal_zone*/temp")
PORTS = sorted(glob.glob("/sys/class/net/en*np[01]/carrier"))
threading.Thread(target=gpu_reader, daemon=True).start()
threading.Thread(target=kmsg_reader, daemon=True).start()

day, f = None, None
while True:
    t = time.time(); d = time.strftime("%Y%m%d", time.localtime(t))
    if d != day:
        if f: f.close()
        day, f = d, open(f"{D}/{d}.log", "a")
        for old in sorted(glob.glob(f"{D}/*.log"))[:-7]: os.remove(old)
    mi = {l.split(":")[0]: int(l.split()[1]) for l in read("/proc/meminfo", "").splitlines() if l.split()[1:2]}
    ms, mf = psi("memory"); cs, _ = psi("cpu")
    temps = [int(read(z, "0")) // 1000 for z in THERMAL]
    carrier = "".join(read(p, "?").strip() or "?" for p in PORTS)
    line = (f"{time.strftime('%H:%M:%S', time.localtime(t))} mem {mi.get('MemAvailable', 0)/1048576:.2f} "
            f"swap {mi.get('SwapFree', 0)/1048576:.1f} psi_mem {ms}/{mf} psi_cpu {cs} load {read('/proc/loadavg').split()[0]} "
            f"cpu_t {max(temps) if temps else '-'} gpu_t {gpu[0]} gpu_w {gpu[1]} sm_mhz {gpu[2]} thr {gpu[3]} cx7 {carrier}")
    while events: line += " | " + events.pop(0)
    f.write(line + "\n"); f.flush(); os.fsync(f.fileno())
    time.sleep(max(0, 1 - (time.time() - t)))
