#!/usr/bin/env python3
"""Isolated real hbbs/hbbr 1.1.15 protocol proof, using only fixture keys.

Run in the test Docker image sharing an isolated hbbs/hbbr network namespace.
This verifies rendezvous, signed identities, the native encrypted wire format
and relay bytes. Actual desktop capture/input remains the desktop E2E suite.
"""
import argparse
import asyncio
import base64
import ctypes
from contextlib import AsyncExitStack
import importlib.util
import os
from pathlib import Path
import ssl
import signal
import subprocess
import sys
import tempfile
import uuid

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
module_path = Path(os.environ.get("MIXEL_BRIDGE_MODULE", ROOT / "infra/relay-ws-bridge/bridge.py"))
spec = importlib.util.spec_from_file_location("mixel_registration_bridge", module_path)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)
field = bridge.length_field


class Crypto:
    """Use the same libsodium primitives as native RustDesk, without key output."""
    def __init__(self):
        self.library = ctypes.CDLL("libsodium.so.23")
        assert self.library.sodium_init() >= 0

    def pair(self, kind):
        public = ctypes.create_string_buffer(32)
        secret = ctypes.create_string_buffer(64 if kind == "sign" else 32)
        assert getattr(self.library, f"crypto_{kind}_keypair")(public, secret) == 0
        return public.raw, secret.raw

    def sign(self, message, secret):
        output = ctypes.create_string_buffer(len(message) + 64)
        length = ctypes.c_ulonglong()
        assert self.library.crypto_sign(output, ctypes.byref(length), message,
                                        ctypes.c_ulonglong(len(message)), secret) == 0
        return output.raw[:length.value]

    def open_signed(self, message, public):
        output = ctypes.create_string_buffer(len(message))
        length = ctypes.c_ulonglong()
        if self.library.crypto_sign_open(output, ctypes.byref(length), message,
                                        ctypes.c_ulonglong(len(message)), public) != 0:
            raise ValueError("signed identity rejected")
        return output.raw[:length.value]

    def box(self, message, public, secret):
        output = ctypes.create_string_buffer(len(message) + 16)
        assert self.library.crypto_box_easy(output, message, ctypes.c_ulonglong(len(message)),
                                            bytes(24), public, secret) == 0
        return output.raw

    def open_box(self, message, public, secret):
        output = ctypes.create_string_buffer(len(message) - 16)
        assert self.library.crypto_box_open_easy(output, message, ctypes.c_ulonglong(len(message)),
                                                 bytes(24), public, secret) == 0
        return output.raw

    def seal(self, message, key, sequence):
        output = ctypes.create_string_buffer(len(message) + 16)
        nonce = sequence.to_bytes(8, "little") + bytes(16)
        assert self.library.crypto_secretbox_easy(output, message, ctypes.c_ulonglong(len(message)),
                                                  nonce, key) == 0
        return output.raw

    def open(self, message, key, sequence):
        output = ctypes.create_string_buffer(len(message) - 16)
        nonce = sequence.to_bytes(8, "little") + bytes(16)
        if self.library.crypto_secretbox_open_easy(output, message, ctypes.c_ulonglong(len(message)),
                                                   nonce, key) != 0:
            raise ValueError("encrypted frame authentication rejected")
        return output.raw


def message(data, number):
    parsed = bridge.fields(data)
    assert len(parsed) == 1 and parsed[number][0] == 2, f"unexpected message type, expected {number}"
    return bridge.fields(parsed[number][1])


async def receive(connection):
    return await asyncio.wait_for(connection.recv(), 10)


async def proof(pin_path, ca_path, idle_seconds):
    crypto = Crypto()
    pin = pin_path.read_text().strip()
    server_public = base64.b64decode(pin, validate=True)
    assert len(server_public) == 32
    tls = ssl.create_default_context(cafile=str(ca_path))
    tls.minimum_version = ssl.TLSVersion.TLSv1_2

    def wss(path="id", context=tls):
        # Every socket is forced into this fixture's loopback namespace. No
        # test message can reach the public rs.mixel.ch DNS address.
        return connect(f"wss://rs.mixel.ch/ws/{path}", host="127.0.0.1", port=443,
                       server_hostname="rs.mixel.ch", ssl=context, proxy=None,
                       compression=None, additional_headers={"X-Real-IP": "203.0.113.99"})

    try:
        async with wss(context=ssl.create_default_context()):
            raise AssertionError("untrusted fixture TLS certificate accepted")
    except ssl.SSLCertVerificationError:
        print("PASS: untrusted TLS certificate rejected; trusted fixture CA and hostname required", flush=True)
    try:
        async with connect("wss://wrong.invalid/ws/id", host="127.0.0.1", port=443,
                           server_hostname="wrong.invalid", ssl=tls, proxy=None):
            raise AssertionError("wrong hostname accepted")
    except ssl.SSLCertVerificationError:
        print("PASS: certificate hostname mismatch rejected", flush=True)

    # This stock behavior is why a reverse proxy alone cannot receive support.
    async with connect("ws://127.0.0.1:21118/", proxy=None) as stock:
        await stock.send(field(15, b""))
        assert await receive(stock) == bytes.fromhex("8201020806")
    print("PASS: exact stock hbbs rejects WebSocket registration with NOT_SUPPORT", flush=True)

    # The proxy gives every connection the same real source address. A stock
    # hbbs per-IP bucket rejects a naive shared-source adapter after 30 keys.
    # Actual 100-guest acknowledgements prove isolated virtual upstream IPs.
    corporate_public, _corporate_secret = crypto.pair("sign")
    corporate_uuids = [uuid.uuid4().bytes for _ in range(100)]
    for wave in (1, 2):
        async with AsyncExitStack() as corporate:
            async def customer(index):
                connection = await corporate.enter_async_context(wss())
                identity = str(946001000 + index).encode()
                await connection.send(field(15, field(1, identity) + field(2, corporate_uuids[index])
                                            + field(3, corporate_public)))
                response = message(await receive(connection), 16)
                assert response.get(1, (0, 0)) == (0, 0), "corporate NAT registration rejected"

            await asyncio.gather(*(customer(index) for index in range(100)))
            print(f"PASS: 100 simultaneous hosts behind one trusted NAT receive real hbbs OK, wave {wave}", flush=True)
    print("PASS: same 100 hosts reconnect within one minute without upstream or gateway NAT throttling", flush=True)

    identity = b"946000001"
    host_public, host_secret = crypto.pair("sign")
    registration = field(15, field(1, identity) + field(2, uuid.uuid4().bytes)
                         + field(3, host_public))
    requests = asyncio.Queue()

    async with wss() as host:
        await host.send(registration)
        assert message(await receive(host), 16).get(1, (0, 0)) == (0, 0)
        print("PASS: real UDP hbbs registration OK reaches trusted WSS host", flush=True)

        async def incoming():
            async for frame in host:
                if frame == b"":
                    await host.send(b"")
                    continue
                parsed = bridge.fields(frame)
                if 7 in parsed and bridge.fields(parsed[7][1]).get(2, (0, 0))[1]:
                    await host.send(registration)
                elif 9 in parsed or 18 in parsed or 12 in parsed:
                    requests.put_nowait(frame)

        reader = asyncio.create_task(incoming())
        try:
            session_uuid = str(uuid.uuid4()).encode()
            async with wss() as controller:
                punch = field(8, field(1, identity) + b"\x10\x02" + field(3, pin.encode())
                              + field(6, b"1.4.6") + b"\x40\x01")
                await controller.send(punch)
                hole = message(await asyncio.wait_for(requests.get(), 10), 9)
                address = hole[1][1]
                response = field(19, field(1, address) + field(2, session_uuid)
                                 + field(3, b"rs.mixel.ch") + field(4, identity)
                                 + field(7, b"1.4.6"))
                async with wss() as host_response:
                    await host_response.send(response)
                    relay_response = message(await receive(controller), 19)
                signed_peer = relay_response[5][1]
                peer = bridge.fields(crypto.open_signed(signed_peer, server_public))
                assert peer[1] == (2, identity) and peer[2] == (2, host_public)
                assert relay_response[2] == (2, session_uuid)
                tampered = signed_peer[:-1] + bytes([signed_peer[-1] ^ 1])
                try:
                    crypto.open_signed(tampered, server_public)
                    raise AssertionError("tampered server identity accepted")
                except ValueError:
                    pass
                print("PASS: incoming PunchHole/RelayResponse route and hbbs-pinned signed host identity verified", flush=True)

            relay_registration = field(18, field(1, identity) + field(2, session_uuid)
                                       + field(6, pin.encode()))
            async with AsyncExitStack() as relay:
                relay_host = await relay.enter_async_context(wss("relay"))
                await relay_host.send(relay_registration)
                # Incoming support establishes the host's pending relay first;
                # the controller then opens its own TLS/WebSocket connection.
                relay_controller = await relay.enter_async_context(wss("relay"))
                await relay_controller.send(relay_registration)
                ephemeral_public, ephemeral_secret = crypto.pair("box")
                signed_ephemeral = crypto.sign(field(1, identity) + field(2, ephemeral_public), host_secret)
                signed_id = field(3, field(1, signed_ephemeral))
                await relay_host.send(signed_id)
                received_signed = message(await receive(relay_controller), 3)[1][1]
                authenticated = bridge.fields(crypto.open_signed(received_signed, peer[2][1]))
                assert authenticated[1] == (2, identity)
                controller_public, controller_secret = crypto.pair("box")
                key = os.urandom(32)
                boxed = crypto.box(key, authenticated[2][1], controller_secret)
                public_key = field(4, field(1, controller_public) + field(2, boxed))
                await relay_controller.send(public_key)
                handshake = message(await receive(relay_host), 4)
                host_key = crypto.open_box(handshake[2][1], handshake[1][1], ephemeral_secret)
                assert host_key == key
                print("PASS: real hbbr WSS pair carries signed ephemeral identity and authenticated boxed session key", flush=True)

                # Same XSalsa20-Poly1305 primitive and 64-bit little-endian
                # sequence nonce used by native tcp::Encrypt/WsFramedStream.
                sequence = 0
                finish = asyncio.get_running_loop().time() + idle_seconds
                while True:
                    sequence += 1
                    payload = b"synthetic-screen-input-file-proof:" + str(sequence).encode()
                    encrypted = crypto.seal(payload, key, sequence)
                    assert payload not in encrypted
                    await relay_controller.send(encrypted)
                    received = await receive(relay_host)
                    assert received == encrypted
                    assert crypto.open(received, host_key, sequence) == payload
                    reply = crypto.seal(b"accepted:" + payload, host_key, sequence)
                    await relay_host.send(reply)
                    returned = await receive(relay_controller)
                    assert returned == reply
                    assert crypto.open(returned, key, sequence) == b"accepted:" + payload
                    if asyncio.get_running_loop().time() >= finish:
                        break
                    await asyncio.sleep(min(5, finish - asyncio.get_running_loop().time()))
                tampered = returned[:-1] + bytes([returned[-1] ^ 1])
                try:
                    crypto.open(tampered, key, sequence)
                    raise AssertionError("tampered encrypted frame accepted")
                except ValueError:
                    pass
                print(f"PASS: bidirectional authenticated encrypted relay bytes survive {idle_seconds:g}s; tampering rejected", flush=True)

            # RegisterPk is sent once. After >30s hbbs must still regard this
            # host as online due to genuine UDP RegisterPeer acknowledgements.
            async with wss() as controller:
                await controller.send(punch)
                assert message(await asyncio.wait_for(requests.get(), 10), 9)[1][0] == 2
                print("PASS: WSS host remains registered past hbbs 30-second expiry without repeated RegisterPk", flush=True)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
    print("PASS: isolated real hbbs/hbbr HTTPS registration, pinned identities and encrypted relay protocol", flush=True)


def fixture(pin_path, idle_seconds, serve_only=False, export_ca=None):
    with tempfile.TemporaryDirectory(prefix="mixel-relay-test-tls-") as temporary:
        folder = Path(temporary)
        ca = folder / "ca.crt"
        commands = [
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=Mixel isolated test CA", "-keyout", str(folder / "ca.key"), "-out", str(ca)],
            ["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=rs.mixel.ch",
             "-keyout", str(folder / "server.key"), "-out", str(folder / "server.csr")],
        ]
        (folder / "extensions.cnf").write_text("subjectAltName=DNS:rs.mixel.ch\n")
        commands.append(["openssl", "x509", "-req", "-in", str(folder / "server.csr"),
                         "-CA", str(ca), "-CAkey", str(folder / "ca.key"), "-CAcreateserial",
                         "-days", "1", "-extfile", str(folder / "extensions.cnf"), "-out", str(folder / "server.crt")])
        for command in commands:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if export_ca:
            export_ca.write_bytes(ca.read_bytes())
        config = folder / "nginx.conf"
        config.write_text(f"""pid {folder}/nginx.pid;
error_log stderr warn;
events {{ worker_connections 1024; }}
http {{ access_log off;
 server {{ listen 443 ssl; server_name rs.mixel.ch;
  ssl_certificate {folder}/server.crt; ssl_certificate_key {folder}/server.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  location = /ws/id {{ proxy_pass http://127.0.0.1:8090; proxy_http_version 1.1;
   proxy_set_header X-Real-IP $remote_addr; proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection upgrade; proxy_read_timeout 3600s; }}
  location = /ws/relay {{ proxy_pass http://127.0.0.1:21119/; proxy_http_version 1.1;
   proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection upgrade;
   proxy_read_timeout 3600s; }}
 }}
}}
""")
        nginx = subprocess.Popen(["nginx", "-c", str(config), "-g", "daemon off;"])
        try:
            async def run():
                gateway = bridge.Gateway(bridge.Settings())
                async with bridge.start_server(gateway):
                    await asyncio.sleep(0.2)
                    if serve_only:
                        stop = asyncio.get_running_loop().create_future()
                        for name in (signal.SIGINT, signal.SIGTERM):
                            asyncio.get_running_loop().add_signal_handler(
                                name, lambda: None if stop.done() else stop.set_result(None))
                        print("READY: isolated test-CA rs.mixel.ch TLS router and registration bridge", flush=True)
                        await stop
                    else:
                        await proof(pin_path, ca, idle_seconds)
                assert gateway.capacity.active == 0
            asyncio.run(run())
        finally:
            nginx.terminate()
            nginx.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-pin", required=True, type=Path)
    parser.add_argument("--idle-seconds", type=float, default=35)
    parser.add_argument("--serve-only", action="store_true", help="keep isolated router available for actual desktop peers")
    parser.add_argument("--export-ca", type=Path, help="export public test CA only, never TLS private key")
    args = parser.parse_args()
    if args.idle_seconds < 31:
        parser.error("idle proof must exceed real hbbs 30-second expiry")
    fixture(args.fixture_pin, args.idle_seconds, args.serve_only, args.export_ca)
