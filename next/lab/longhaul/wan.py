#!/usr/bin/env python3
"""User-space WAN emulator between two TUN devices (no netem in this kernel).
Each direction: drop-tail bottleneck queue at RATE bit/s holding at most QUEUE ms, then a one-way
DELAY with JITTER (packets keep their order), then random LOSS. Prints per-direction counters."""
import os, fcntl, struct, select, time, random, heapq, sys, json, signal
TUNSETIFF = 0x400454ca; IFF_TUN = 0x0001; IFF_NO_PI = 0x1000
def tun(name):
    fd = os.open('/dev/net/tun', os.O_RDWR)
    fcntl.ioctl(fd, TUNSETIFF, struct.pack('16sH', name.encode(), IFF_TUN | IFF_NO_PI))
    os.set_blocking(fd, False)
    return fd
cfg = json.loads(sys.argv[1])  # {"a2b": {...}, "b2a": {...}} with delay_ms, jitter_ms, loss, rate_mbps, queue_ms
fa, fb = tun('wana'), tun('wanb')
open('/tmp/wan.ready', 'w').write('1')
class Dir:
    def __init__(s, c, out):
        s.c = c; s.out = out; s.free_at = 0.0; s.last_dep = 0.0
        s.sent = s.dropped_q = s.lost = s.bytes = 0
    def push(s, pkt, now, heap, seq):
        c = s.c
        rate = c.get('rate_mbps', 0) * 1e6 / 8
        start = max(now, s.free_at)
        if rate:
            if (start - now) * 1000 > c.get('queue_ms', 200):
                s.dropped_q += 1; return
            s.free_at = start + len(pkt) / rate
            done = s.free_at
        else:
            done = now
        if random.random() < c.get('loss', 0):
            s.lost += 1; return
        j = c.get('jitter_ms', 0)
        dep = done + (c.get('delay_ms', 0) + (random.uniform(-j, j) if j else 0)) / 1000
        dep = max(dep, s.last_dep); s.last_dep = dep
        heapq.heappush(heap, (dep, seq, s.out, pkt)); s.sent += 1; s.bytes += len(pkt)
dirs = {fa: Dir(cfg['a2b'], fb), fb: Dir(cfg['b2a'], fa)}
heap = []; seq = 0; report = time.monotonic() + 5
def reload(*_):
    global cfg
    try:
        cfg = json.load(open('/tmp/wan.json')); dirs[fa].c = cfg['a2b']; dirs[fb].c = cfg['b2a']
        print('reloaded', cfg, flush=True)
    except Exception as e: print('reload failed', e, flush=True)
signal.signal(signal.SIGHUP, reload)
while True:
    now = time.monotonic()
    timeout = max(0, heap[0][0] - now) if heap else 0.05
    try:
        r, _, _ = select.select([fa, fb], [], [], min(timeout, 0.05))
    except InterruptedError:
        continue
    now = time.monotonic()
    for fd in r:
        for _ in range(64):
            try: pkt = os.read(fd, 65535)
            except BlockingIOError: break
            seq += 1; dirs[fd].push(pkt, now, heap, seq)
    while heap and heap[0][0] <= now:
        _, _, out, pkt = heapq.heappop(heap)
        try: os.write(out, pkt)
        except OSError: pass
    if now > report:
        report = now + 5
        print('a2b', vars(dirs[fa]) | {'out': None, 'c': None}, 'b2a', vars(dirs[fb]) | {'out': None, 'c': None}, flush=True)
