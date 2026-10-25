#!/usr/bin/env python3
"""cabling.py — check the 4-node CX7 ring from lldp.py output and say exactly what to fix.

    cat lldp-output-from-all-nodes | python3 cabling.py --head HOSTNAME [--names h1=10.0.0.11,h2=...]

Input lines (fabric/lldp.py listen): "hostA:enp1s0f1np1 <- hostB:enP2p1s0f0np0".
The ring needs: every node has one cable on port 0 (p0) and one on port 1 (p1); every cable joins one node's p0 to
the next node's p1; the four cables form one cycle through all four nodes. Prints the ring order starting at the
head (the order NODE_LAN in cluster.env must have) and a last line "ORDER h1 h2 h3 h4" when the ring is correct.
Exit 0: correct. 1: wrong but fixable by moving cables (fix printed). 2: missing/unknown links.
"""
import argparse, itertools, re, sys

A = argparse.ArgumentParser()
A.add_argument("--head", required=True)
A.add_argument("--names", default="", help="host=label pairs for the printout (e.g. host=LAN address)")
ARGS = A.parse_args()
LABEL = dict(x.split("=", 1) for x in ARGS.names.split(",") if "=" in x)
def lab(h): return f"{h} ({LABEL[h]})" if h in LABEL else h
def port(ifname):  # both PCIe functions of a physical port belong to that port
    return "p0" if "f0np0" in ifname else "p1" if "f1np1" in ifname else None

links = set()
for line in sys.stdin:
    m = re.match(r"\s*([\w.-]+):(\S+) <- ([\w.-]+):(\S+)\s*$", line)
    if not m:
        continue
    a, ai, b, bi = m.groups()
    pa, pb = port(ai), port(bi)
    if pa and pb and a != b:
        links.add(tuple(sorted([(a, pa), (b, pb)])))

hosts = sorted({h for l in links for h, _ in l} | {ARGS.head})
print("links seen (node:port <-> node:port):")
for (a, pa), (b, pb) in sorted(links):
    print(f"  {lab(a)}:{pa} <-> {lab(b)}:{pb}")

problems = []
by_port = {}
for l in links:
    for end in l:
        by_port.setdefault(end, set()).add(l)
for h in hosts:
    for p in ("p0", "p1"):
        n = len(by_port.get((h, p), ()))
        if n == 0:
            problems.append(f"{lab(h)} {p}: no cable seen. Plug a cable in, or check the other end is in a Spark (link LED on?).")
        elif n > 1:
            problems.append(f"{lab(h)} {p}: sees {n} different peers; that is impossible with one cable per port. Re-run the check.")
if len(hosts) != 4:
    problems.append(f"expected 4 nodes, saw {len(hosts)}: {', '.join(hosts)}. A node is unreachable or not cabled at all.")
pairs = [tuple(sorted((a, b))) for (a, _), (b, _) in links]
for p in set(pairs):
    if pairs.count(p) > 1:
        problems.append(f"{lab(p[0])} and {lab(p[1])} are connected by two cables. The ring needs each node cabled to two "
                        f"different nodes: move one of these cables to the node it is missing.")
if problems:
    print("\nNOT A RING YET:")
    for p in problems:
        print("  - " + p)
    sys.exit(2)

# graph is a 4-cycle; check orientation (every link p0<->p1)
def bad_links(flip):
    def pt(h, p): return {"p0": "p1", "p1": "p0"}[p] if h in flip else p
    return [l for l in links if pt(*l[0]) == pt(*l[1])]

if not bad_links(set()):
    peer = {}                       # (host, port) -> host at the other end
    for (a, pa), (b, pb) in links:
        peer[(a, pa)] = b; peer[(b, pb)] = a
    order = [ARGS.head]
    while len(order) < 4:           # follow each node's port 0 to the next node
        order.append(peer[(order[-1], "p0")])
    print("\nRING OK: " + " -> ".join(f"rk{i + 1} {lab(h)}" for i, h in enumerate(order)) + " -> back to rk1")
    print("ORDER " + " ".join(order))
    sys.exit(0)

# find the fewest nodes whose two cables must be swapped (port 0 <-> port 1)
for k in range(1, 5):
    for flip in itertools.combinations(hosts, k):
        if not bad_links(set(flip)):
            print("\nCABLES IN THE WRONG PORTS: every cable must join one node's port 0 to the next node's port 1.")
            for (a, pa), (b, pb) in bad_links(set()):
                print(f"  - {lab(a)}:{pa} is cabled to {lab(b)}:{pb} (same port number on both ends)")
            print("Fix: on " + ", ".join(lab(h) for h in flip) + ": swap the two cables between port 0 and port 1"
                  " (unplug both, plug each into the other port). Then run the check again.")
            sys.exit(1)
print("\nThe cabling could not be repaired by swapping ports; check every cable against the diagram in README.md.")
sys.exit(2)
