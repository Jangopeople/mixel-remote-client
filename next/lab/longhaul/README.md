# Long-haul lab (Mauritius line)

Real sessions (production engine, WebRTC, OpenH264) between two processes in two network
namespaces, joined only through a user-space line emulator (`wan.py`: bottleneck rate, drop-tail
queue, one-way delay with jitter, random loss; the cloud kernels have no netem).

    apt-get install iproute2 xvfb
    cargo +1.99 test --release -p mixel_engine --lib --no-run
    HEAVY=200 ./matrix.sh mytag        # six line profiles, 45 s each, results in out/

Each run prints one `RESULT` line from the technician side: pictures a second, picture delay
(screen drawn -> picture decoded, p50/p95/max), pointer-to-picture delay, and time frozen.
`HBIN=` / `VBIN=` run an older engine on one side (compatibility).

Profiles (`matrix.sh`, 210 ms round trip): A 50 Mbit/s clean, B 4 Mbit/s, C 4 Mbit/s 0.5 % loss,
D 20 Mbit/s 1 % loss, E 1.5 Mbit/s, F 8 Mbit/s 2 % loss.
