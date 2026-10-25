import socket,sys,time,select
IF=["enp1s0f0np0","enp1s0f1np1","enP2p1s0f0np0","enP2p1s0f1np1"]; ET=0x88b6   # IEEE local experimental; not 0x88b5, which the ring mesh forwards in hardware
me=socket.gethostname(); mode=sys.argv[1]
if mode=="listen":
    ss={}
    for i in IF:
        s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(ET)); s.bind((i,0)); ss[s]=i
    seen=set(); end=time.time()+float(sys.argv[2])
    while time.time()<end:
        r,_,_=select.select(list(ss),[],[],0.5)
        for s in r:
            d=s.recv(2000)[14:].split(b"\0")[0].decode()
            if not d.startswith(me+":"): seen.add(f"{me}:{ss[s]} <- {d}")
    print("\n".join(sorted(seen)))
else:
    for _ in range(5):
        for i in IF:
            s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW); s.bind((i,0))
            mac=bytes.fromhex(open(f"/sys/class/net/{i}/address").read().strip().replace(":",""))
            s.send(b"\xff"*6+mac+ET.to_bytes(2,"big")+f"{me}:{i}".encode().ljust(46,b"\0"))
        time.sleep(0.3)
