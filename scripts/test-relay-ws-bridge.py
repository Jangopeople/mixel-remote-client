#!/usr/bin/env python3
"""Exercise Linux gateway through local WS/UDP sockets, no live relay.

Run in its built Docker image when the development host isn't Linux; Linux
provides the 127/8 virtual source addresses without host interface mutations.
"""
import asyncio
from dataclasses import replace
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import sys
import unittest
from urllib.request import urlopen

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus

ROOT = Path(__file__).resolve().parents[1]
module_path = Path(os.environ.get("MIXEL_BRIDGE_MODULE", ROOT / "infra/relay-ws-bridge/bridge.py"))
spec = importlib.util.spec_from_file_location("mixel_registration_bridge", module_path)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


def register(identity=b"123456", uuid=b"synthetic-uuid", public_key=b"k" * 32):
    return bridge.length_field(15, bridge.length_field(1, identity)
                               + bridge.length_field(2, uuid)
                               + bridge.length_field(3, public_key))


OK = bridge.length_field(16, b"")
HEARTBEAT = bridge.length_field(7, b"")
REQUEST_PK = bridge.length_field(7, b"\x10\x01")


class HbbsFixture(asyncio.DatagramProtocol):
    """Observe transport boundaries only; production gateway logic stays real."""
    def __init__(self):
        self.seen = asyncio.Queue()
        self.addresses = set()
        self.respond = True
        self.need_key = False

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, address):
        self.addresses.add(address)
        self.seen.put_nowait((data, address))
        if not self.respond:
            return
        number, _payload = bridge.envelope(data)
        self.transport.sendto(OK if number == 15 else
                              REQUEST_PK if self.need_key else HEARTBEAT, address)


class ProtocolTests(unittest.TestCase):
    def test_registration_preserves_optional_fields_and_rejects_malformed(self):
        identity = b"synthetic_customer_123"
        self.assertEqual(bridge.registration(register(identity)), identity)
        for malformed in (b"", "text", b"\x00", b"\x7a\x04\x00", b"\x7a\x00",
                          register(b"123"), register(public_key=b"short"),
                          register(b"bad id"), register(uuid=b""),
                          register() + b"\x7a\x00",
                          bridge.length_field(15, bridge.length_field(1, b"123456") * 2)):
            with self.subTest(frame=repr(malformed)):
                with self.assertRaises(bridge.ProtocolError):
                    bridge.registration(malformed)

    def test_source_pool_and_rate_are_bounded_without_shared_customer_ip(self):
        capacity = bridge.Capacity(replace(bridge.Settings(), max_sessions=2,
                                           per_ip_sessions=1, registrations_per_minute=1))
        first = capacity.acquire("192.0.2.1")
        second = capacity.acquire("192.0.2.2")
        self.assertNotEqual(first, second)
        self.assertTrue(ipaddress.ip_address(first).is_loopback)
        with self.assertRaises(bridge.ProtocolError):
            capacity.acquire("192.0.2.1")
        with self.assertRaises(bridge.ProtocolError):
            capacity.acquire("192.0.2.3")
        capacity.registering("192.0.2.1")
        with self.assertRaises(bridge.ProtocolError):
            capacity.registering("192.0.2.1")
        capacity.release("192.0.2.1", first)
        capacity.release("192.0.2.2", second)
        self.assertEqual(capacity.active, 0)
        self.assertEqual(len(capacity.free_ips), 2)

    def test_global_registration_history_cannot_exceed_its_memory_bound(self):
        capacity = bridge.Capacity(replace(bridge.Settings(), max_sessions=1,
                                           registrations_per_minute=32))
        for _ in range(16):
            capacity.registering("192.0.2.1")
        with self.assertRaises(bridge.ProtocolError):
            capacity.registering("192.0.2.2")
        self.assertEqual(len(capacity.recent_events), 16)
        self.assertNotIn("192.0.2.2", capacity.recent)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        loop = asyncio.get_running_loop()
        self.udp, self.hbbs = await loop.create_datagram_endpoint(HbbsFixture,
                                                               local_addr=("127.0.0.1", 0))
        self.controller_messages = asyncio.Queue()

        async def upstream(websocket):
            async for message in websocket:
                self.controller_messages.put_nowait(message)
                await websocket.send(message)

        self.upstream = await serve(upstream, "127.0.0.1", 0)
        port = self.upstream.sockets[0].getsockname()[1]
        settings = bridge.Settings(port=0, udp_port=self.udp.get_extra_info("sockname")[1],
                                   upstream_ws=f"ws://127.0.0.1:{port}/",
                                   heartbeat=0.05, upstream_timeout=0.3,
                                   client_timeout=0.3, first_timeout=0.2)
        self.gateway = bridge.Gateway(settings)
        self.server = await bridge.start_server(self.gateway)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/ws/id"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.upstream.close()
        await self.upstream.wait_closed()
        self.udp.close()
        self.assertEqual(self.gateway.capacity.active, 0)

    def client(self, source="192.0.2.1", **kwargs):
        return connect(self.url, additional_headers={"X-Real-IP": source},
                       proxy=None, compression=None, **kwargs)

    async def receive(self, websocket):
        return await asyncio.wait_for(websocket.recv(), 1)

    async def test_controller_path_preserves_all_binary_bytes(self):
        first = bridge.length_field(8, b"\x0a\x06123456")
        opaque = b"\x00\xffsigned-peer-data\x80\x00"
        async with self.client() as websocket:
            for frame in (first, opaque, b""):
                await websocket.send(frame)
                self.assertEqual(await self.receive(websocket), frame)
                self.assertEqual(await self.controller_messages.get(), frame)
        self.assertTrue(self.hbbs.seen.empty())

    async def test_loopback_status_contains_only_bounded_connection_counts(self):
        async with self.client() as websocket:
            await websocket.send(register())
            self.assertEqual(await self.receive(websocket), OK)
            url = self.url.replace("ws://", "http://").replace("/ws/id", "/status")

            def status():
                with urlopen(url, timeout=1) as response:
                    return json.loads(response.read())

            self.assertEqual(await asyncio.to_thread(status), {
                "active_connections": 1, "connection_limit": 512})

    async def test_host_ack_heartbeat_and_server_requests_are_real(self):
        first = register()
        async with self.client() as websocket:
            await websocket.send(first)
            self.assertEqual(await self.receive(websocket), OK)
            data, address = await self.hbbs.seen.get()
            self.assertEqual(data, first)
            self.assertEqual(await self.receive(websocket), HEARTBEAT)
            heartbeat, again = await self.hbbs.seen.get()
            self.assertEqual(bridge.envelope(heartbeat), (6, bridge.length_field(1, b"123456")))
            self.assertEqual(address, again)
            self.assertEqual(await self.receive(websocket), b"")
            await websocket.send(b"")
            request = bridge.length_field(18, b"\x0a\x06123456\x1a\x04uuid")
            self.udp.sendto(request, address)
            while True:
                frame = await self.receive(websocket)
                if frame == request:
                    break
                if frame == b"":
                    await websocket.send(b"")
            self.assertTrue(self.controller_messages.empty())

    async def test_two_host_sessions_have_distinct_socket_routing(self):
        # Both customers share a real corporate NAT address. Their upstream
        # source IPs must still stay distinct for hbbs's stock registration cap.
        async with self.client("192.0.2.1") as first, self.client("192.0.2.1") as second:
            await first.send(register(b"123456"))
            await second.send(register(b"654321"))
            self.assertEqual(await self.receive(first), OK)
            self.assertEqual(await self.receive(second), OK)
            _data, first_address = await self.hbbs.seen.get()
            _data, second_address = await self.hbbs.seen.get()
            self.assertNotEqual(first_address, second_address)
            self.assertNotEqual(first_address[0], second_address[0])
            marker = bridge.length_field(18, b"second-host-only")
            self.udp.sendto(marker, second_address)
            self.assertEqual(await self.receive(second), marker)
            # First still receives its own real registration heartbeat only.
            self.assertEqual(await self.receive(first), HEARTBEAT)

    async def test_missing_and_forged_proxy_sources_are_rejected(self):
        for headers in ({}, {"X-Real-IP": "bad"}, [("X-Real-IP", "192.0.2.1"),
                                                    ("X-Real-IP", "192.0.2.2")]):
            with self.subTest(headers=headers):
                with self.assertRaises(InvalidStatus) as error:
                    async with connect(self.url, additional_headers=headers, proxy=None):
                        pass
                self.assertEqual(error.exception.response.status_code, 400)
        with self.assertRaises(InvalidStatus) as error:
            async with connect(self.url + "?redirect=attacker", proxy=None):
                pass
        self.assertEqual(error.exception.response.status_code, 404)

    async def test_malformed_host_never_reaches_udp_or_upstream(self):
        async with self.client() as websocket:
            await websocket.send(b"\x7a\x00")
            with self.assertRaises(ConnectionClosed) as error:
                await self.receive(websocket)
            self.assertEqual(error.exception.rcvd.code, 1008)
        self.assertTrue(self.hbbs.seen.empty())
        self.assertTrue(self.controller_messages.empty())

    async def test_no_forged_online_when_hbbs_is_silent(self):
        self.hbbs.respond = False
        async with self.client() as websocket:
            await websocket.send(register())
            with self.assertRaises(ConnectionClosed) as error:
                await self.receive(websocket)
            self.assertEqual(error.exception.rcvd.code, 1008)

    async def test_missing_application_echo_closes_even_when_upstream_answers(self):
        async with self.client() as websocket:
            await websocket.send(register())
            self.assertEqual(await self.receive(websocket), OK)
            with self.assertRaises(ConnectionClosed) as error:
                while True:
                    await self.receive(websocket)  # deliberately never echo
            self.assertEqual(error.exception.rcvd.code, 1008)

    async def test_upstream_loss_closes_an_active_client(self):
        async with self.client() as websocket:
            await websocket.send(register())
            self.assertEqual(await self.receive(websocket), OK)
            self.hbbs.respond = False

            async def pulse():
                while True:
                    await asyncio.sleep(0.05)
                    await websocket.send(b"")

            active = asyncio.create_task(pulse())
            try:
                with self.assertRaises(ConnectionClosed) as error:
                    await self.receive(websocket)
                self.assertEqual(error.exception.rcvd.code, 1008)
            finally:
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)

    async def test_closed_client_stops_udp_registration_and_releases_capacity(self):
        async with self.client() as websocket:
            await websocket.send(register())
            self.assertEqual(await self.receive(websocket), OK)
        await asyncio.sleep(0.1)
        before = self.hbbs.seen.qsize()
        await asyncio.sleep(0.15)
        self.assertEqual(self.hbbs.seen.qsize(), before)
        self.assertEqual(self.gateway.capacity.active, 0)

    async def test_hbbs_restart_requests_real_key_reregistration(self):
        async with self.client() as websocket:
            await websocket.send(register())
            self.assertEqual(await self.receive(websocket), OK)
            self.hbbs.need_key = True
            self.assertEqual(await self.receive(websocket), REQUEST_PK)
            self.assertEqual(await self.receive(websocket), b"")
            await websocket.send(register())
            self.assertEqual(await self.receive(websocket), OK)
            self.assertEqual(self.hbbs.seen.qsize(), 3)

    async def test_host_rejects_nonregistration_write_after_registration(self):
        async with self.client() as websocket:
            await websocket.send(register())
            self.assertEqual(await self.receive(websocket), OK)
            await websocket.send(bridge.length_field(8, b"unauthorized-other-mode"))
            with self.assertRaises(ConnectionClosed) as error:
                await self.receive(websocket)
            self.assertEqual(error.exception.rcvd.code, 1008)


if __name__ == "__main__":
    unittest.main(verbosity=2)
