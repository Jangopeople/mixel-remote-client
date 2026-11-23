"""Regression tests for coturn peer ACL and quota guard (incident 2026-11-23)."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PREP = _load("prepare_next_service_qa", "scripts/local/prepare-next-service-qa.py")
HEALTH = _load("mixel_remote_health", "next/deployment/ops/mixel-remote-health.py")


class TurnConfigGuardTest(unittest.TestCase):
    def test_generated_turn_configuration_passes_guard(self) -> None:
        cfg = PREP.turn_configuration("a" * 64)
        self.assertNotIn("denied-peer-ip=::-ffff", cfg)
        self.assertIn("denied-peer-ip=::1", cfg)
        self.assertIn("user-quota=32", cfg)
        self.assertIn("total-quota=256", cfg)
        summary = HEALTH.validate_turn_config_text(cfg)
        self.assertIn("user-quota=32", summary)

    def test_ipv6_wildcard_is_rejected(self) -> None:
        bad = PREP.turn_configuration("a" * 64) + "\ndenied-peer-ip=::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff\n"
        with self.assertRaises(HEALTH.CheckError) as ctx:
            HEALTH.validate_turn_config_text(bad)
        self.assertIn("blocks public IPv4 peers", str(ctx.exception))

    def test_low_quotas_are_rejected(self) -> None:
        bad = PREP.turn_configuration("a" * 64).replace("user-quota=32", "user-quota=8")
        with self.assertRaises(HEALTH.CheckError) as ctx:
            HEALTH.validate_turn_config_text(bad)
        self.assertIn("user-quota=8", str(ctx.exception))

    def test_missing_ssrf_block_is_rejected(self) -> None:
        bad = PREP.turn_configuration("a" * 64).replace("denied-peer-ip=10.0.0.0-10.255.255.255", "")
        with self.assertRaises(HEALTH.CheckError) as ctx:
            HEALTH.validate_turn_config_text(bad)
        self.assertIn("10.0.0.0-10.255.255.255", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
