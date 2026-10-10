#!/usr/bin/env python3
"""Compatibility gateway for RustDesk OSS 1.1.15 host registration.

Bind behind the existing TLS reverse proxy. No relay keys or database are read.
Controller messages retain the upstream WebSocket path; each host has a separate
connected UDP socket and its real hbbs responses are forwarded unchanged.
"""
import argparse
import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
import ipaddress
import json
import logging
import signal
import socket
import ssl
import time

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

MAX_MESSAGE = 4096
LOGGER = logging.getLogger("mixel.registration")


class ProtocolError(ValueError):
    pass


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ProtocolError("truncated protobuf integer")
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            raise ProtocolError("oversized protobuf integer")
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ProtocolError("oversized protobuf integer")


def fields(data):
    if not isinstance(data, bytes) or len(data) > MAX_MESSAGE:
        raise ProtocolError("binary protobuf frame required")
    result = {}
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0 or number > 536870911 or number in result:
            raise ProtocolError("invalid or duplicate protobuf field")
        if wire == 0:
            value, offset = read_varint(data, offset)
        elif wire in (1, 5):
            count = 8 if wire == 1 else 4
            value = data[offset:offset + count]
            offset += count
            if len(value) != count:
                raise ProtocolError("truncated protobuf field")
        elif wire == 2:
            count, offset = read_varint(data, offset)
            if count > len(data) - offset:
                raise ProtocolError("truncated protobuf field")
            value = data[offset:offset + count]
            offset += count
        else:
            raise ProtocolError("unsupported protobuf wire type")
        result[number] = (wire, value)
        if len(result) > 64:
            raise ProtocolError("too many protobuf fields")
    return result


def envelope(data):
    content = fields(data)
    if len(content) != 1:
        raise ProtocolError("one rendezvous message required")
    number, (wire, payload) = next(iter(content.items()))
    if wire != 2 or not 6 <= number <= 26:
        raise ProtocolError("invalid rendezvous message")
    return number, payload


def length_field(number, value):
    return varint(number * 8 + 2) + varint(len(value)) + value


def registration(data):
    number, payload = envelope(data)
    if number != 15:
        raise ProtocolError("host must register its key first")
    content = fields(payload)
    required = [content.get(number) for number in (1, 2, 3)]
    if any(value is None or value[0] != 2 for value in required):
        raise ProtocolError("incomplete key registration")
    identity, uuid, public_key = [value[1] for value in required]
    if not 6 <= len(identity) <= 64 or not 1 <= len(uuid) <= 256 or len(public_key) != 32:
        raise ProtocolError("invalid key registration size")
    if any(byte not in b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-" for byte in identity):
        raise ProtocolError("invalid registration identity")
    # Unknown optional fields remain opaque and travel to hbbs unchanged.
    return identity


@dataclass(frozen=True)
class Settings:
    listen: str = "127.0.0.1"
    port: int = 8090
    udp_host: str = "127.0.0.1"
    udp_port: int = 21116
    upstream_ws: str = "ws://127.0.0.1:21118/"
    max_sessions: int = 512
    per_ip_sessions: int = 256
    registrations_per_minute: int = 1024
    heartbeat: float = 10.0
    upstream_timeout: float = 30.0
    client_timeout: float = 30.0
    first_timeout: float = 10.0


class Capacity:
    """Bounded real-client quotas and virtual source addresses, never peer IDs."""
    def __init__(self, settings):
        self.settings = settings
        self.clients = {}
        self.recent = {}
        self.recent_events = deque()
        self.free_ips = deque(str(ipaddress.IPv4Address(0x7F400001 + index))
                              for index in range(settings.max_sessions))
        self.active = 0

    def acquire(self, real_ip):
        now = time.monotonic()
        self.recent = {ip: timestamps for ip, timestamps in self.recent.items()
                       if timestamps and now - timestamps[-1] < 60}
        if self.active >= self.settings.max_sessions:
            raise ProtocolError("gateway capacity reached")
        if real_ip not in self.clients:
            # Closed-source quota entries cannot grow without bound either.
            if len(self.recent) >= self.settings.max_sessions * 8 and real_ip not in self.recent:
                raise ProtocolError("gateway source capacity reached")
            self.clients[real_ip] = 0
        if self.clients[real_ip] >= self.settings.per_ip_sessions:
            raise ProtocolError("source connection capacity reached")
        self.clients[real_ip] += 1
        self.active += 1
        # Every session gets its own UDP source IP as well as source port.
        # hbbs's stock per-IP registration throttle must not merge the valid
        # desktops behind one corporate NAT into one upstream identity bucket.
        return self.free_ips.popleft()

    def release(self, real_ip, source_ip):
        self.clients[real_ip] -= 1
        self.active -= 1
        self.free_ips.append(source_ip)
        if self.clients[real_ip] == 0:
            del self.clients[real_ip]

    def registering(self, real_ip):
        now = time.monotonic()
        while self.recent_events and now - self.recent_events[0] >= 60:
            self.recent_events.popleft()
        if len(self.recent_events) >= self.settings.max_sessions * 16:
            raise ProtocolError("gateway registration rate exceeded")
        timestamps = self.recent.setdefault(real_ip, deque())
        while timestamps and now - timestamps[0] >= 60:
            timestamps.popleft()
        if len(timestamps) >= self.settings.registrations_per_minute:
            raise ProtocolError("source registration rate exceeded")
        timestamps.append(now)
        self.recent_events.append(now)


class Datagram(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.messages = asyncio.Queue(maxsize=16)
        self.failed = asyncio.get_running_loop().create_future()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, _address):
        if len(data) > MAX_MESSAGE or self.messages.full():
            self.error_received(ProtocolError("upstream datagram limit exceeded"))
            return
        self.messages.put_nowait(data)

    def error_received(self, error):
        if not self.failed.done():
            self.failed.set_exception(error)
        if self.transport:
            self.transport.close()


async def run_until_first(tasks):
    tasks = [asyncio.create_task(task) for task in tasks]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class Gateway:
    def __init__(self, settings):
        self.settings = settings
        self.capacity = Capacity(settings)

    def process_request(self, connection, request):
        if request.path == "/healthz":
            return connection.respond(HTTPStatus.OK, "ok\n")
        if request.path == "/status":
            return connection.respond(HTTPStatus.OK, json.dumps({
                "active_connections": self.capacity.active,
                "connection_limit": self.settings.max_sessions,
            }) + "\n")
        if request.path != "/ws/id":
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        # The service is loopback-only. nginx must replace, never append, this
        # header from its real socket peer. Forwarded-For is deliberately unused.
        try:
            if not ipaddress.ip_address(connection.remote_address[0]).is_loopback:
                return connection.respond(HTTPStatus.FORBIDDEN, "loopback proxy required\n")
            values = request.headers.get_all("X-Real-IP")
            if len(values) != 1:
                raise ValueError("missing or duplicate proxy source")
            ipaddress.ip_address(values[0])
        except (ValueError, TypeError):
            return connection.respond(HTTPStatus.BAD_REQUEST, "trusted proxy source required\n")
        return None

    async def handler(self, websocket):
        real_ip = str(ipaddress.ip_address(websocket.request.headers["X-Real-IP"]))
        acquired = False
        try:
            source_ip = self.capacity.acquire(real_ip)
            acquired = True
            first = await asyncio.wait_for(websocket.recv(), self.settings.first_timeout)
            number, _payload = envelope(first)
            if number == 15:
                await self.host(websocket, first, real_ip, source_ip)
            else:
                await self.controller(websocket, first)
        except (ProtocolError, asyncio.TimeoutError):
            await websocket.close(code=1008, reason="registration transport policy")
        except ConnectionClosed:
            pass
        except (OSError, EOFError):
            await websocket.close(code=1011, reason="rendezvous unavailable")
        except Exception:
            # A payload, identity, token or proxy source must never enter logs.
            LOGGER.error("registration transport failed")
            await websocket.close(code=1011, reason="registration transport failed")
        finally:
            if acquired:
                self.capacity.release(real_ip, source_ip)

    async def controller(self, websocket, first):
        async with connect(self.settings.upstream_ws, proxy=None, compression=None,
                           max_size=MAX_MESSAGE, max_queue=16, open_timeout=10,
                           close_timeout=3) as upstream:
            await upstream.send(first)

            async def copy(source, destination):
                async for message in source:
                    if not isinstance(message, bytes):
                        raise ProtocolError("binary controller message required")
                    await destination.send(message)

            await run_until_first([copy(websocket, upstream), copy(upstream, websocket)])
            await websocket.close(code=1000, reason="rendezvous completed")

    async def host(self, websocket, first, real_ip, source_ip):
        identity = registration(first)
        self.capacity.registering(real_ip)
        loop = asyncio.get_running_loop()
        transport, datagram = await loop.create_datagram_endpoint(
            Datagram, local_addr=(source_ip, 0),
            remote_addr=(self.settings.udp_host, self.settings.udp_port),
            family=socket.AF_INET)
        registered = False
        last_upstream = loop.time()
        last_client = loop.time()
        transport.sendto(first)

        async def incoming_client():
            nonlocal identity, registered, last_client
            async for message in websocket:
                if message == b"":
                    last_client = loop.time()
                    continue
                identity = registration(message)
                self.capacity.registering(real_ip)
                registered = False
                last_client = loop.time()
                transport.sendto(message)

        async def incoming_udp():
            nonlocal registered, last_upstream
            while True:
                message = await datagram.messages.get()
                number, payload = envelope(message)
                if number not in (7, 9, 12, 14, 16, 18):
                    raise ProtocolError("unexpected upstream host message")
                last_upstream = loop.time()
                if number == 16:
                    response = fields(payload)
                    result = response.get(1, (0, 0))
                    if result[0] != 0:
                        raise ProtocolError("invalid registration response")
                    registered = result[1] == 0
                elif number == 7:
                    response = fields(payload)
                    request_pk = response.get(2, (0, 0))
                    if request_pk[0] != 0:
                        raise ProtocolError("invalid registration heartbeat")
                    if request_pk[1]:
                        registered = False
                # No forged OK or signed peer identity. Actual server bytes
                # retain any permissions, errors and future compatible fields.
                await websocket.send(message)
                if number == 7:
                    await websocket.send(b"")  # native client's application echo

        async def heartbeat():
            while True:
                await asyncio.sleep(self.settings.heartbeat)
                if loop.time() - last_client > self.settings.client_timeout:
                    raise asyncio.TimeoutError("client application heartbeat lost")
                if loop.time() - last_upstream > self.settings.upstream_timeout:
                    raise asyncio.TimeoutError("upstream registration heartbeat lost")
                if registered:
                    transport.sendto(length_field(6, length_field(1, identity)))

        async def failure():
            await datagram.failed

        try:
            await run_until_first([incoming_client(), incoming_udp(), heartbeat(), failure()])
        finally:
            transport.close()
            # Retrieve cancellation/error futures to avoid event-loop warnings.
            if not datagram.failed.done():
                datagram.failed.cancel()
            elif not datagram.failed.cancelled():
                with suppress(Exception):
                    datagram.failed.exception()


def start_server(gateway, tls=None):
    settings = gateway.settings
    return serve(gateway.handler, settings.listen, settings.port,
                 process_request=gateway.process_request, ssl=tls,
                 compression=None, max_size=MAX_MESSAGE, max_queue=16,
                 ping_interval=20, ping_timeout=20, close_timeout=3,
                 open_timeout=10, server_header=None)


async def main(settings, cert=None, key=None):
    tls = None
    if bool(cert) != bool(key):
        raise ValueError("both test TLS certificate and key required")
    if cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(cert, key)
    stop = asyncio.get_running_loop().create_future()
    for name in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(
            name, lambda: None if stop.done() else stop.set_result(None))
    async with start_server(Gateway(settings), tls):
        LOGGER.info("registration compatibility gateway listening")
        await stop


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=21116)
    parser.add_argument("--upstream-ws", default="ws://127.0.0.1:21118/")
    parser.add_argument("--test-cert")
    parser.add_argument("--test-key")
    args = parser.parse_args()
    if not ipaddress.ip_address(args.listen).is_loopback:
        parser.error("gateway must bind loopback behind the trusted TLS proxy")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main(Settings(listen=args.listen, port=args.port,
                              udp_host=args.udp_host, udp_port=args.udp_port,
                              upstream_ws=args.upstream_ws), args.test_cert, args.test_key))
