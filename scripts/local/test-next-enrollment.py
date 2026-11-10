#!/usr/bin/env python3
"""Verify enrollment only in a new, repo-owned private PostgreSQL fixture.

No remote URL, DSN or existing data directory is accepted. Local trust auth is
limited to a random mode-0700 Unix socket directory; TCP listening is disabled.
The owned server is stopped even if a SQL assertion fails.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New folder inside this repo's _scratch")
    parser.add_argument("--postgres-bin", default="/opt/homebrew/opt/postgresql@17/bin")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to((ROOT / "_scratch").resolve()) or output == (ROOT / "_scratch").resolve():
        parser.error("Output must be a new subfolder inside this repo's _scratch")
    if output.exists():
        parser.error("Refusing to reuse an existing fixture folder")
    binaries = Path(args.postgres_bin).resolve()
    for name in ("initdb", "pg_ctl", "psql", "postgres"):
        if not (binaries / name).is_file():
            parser.error(f"PostgreSQL server tool is unavailable: {name}")
    output.mkdir(parents=True, mode=0o700)
    data = output / "data"
    socket = Path(tempfile.mkdtemp(prefix="mixel-remote-pg-"))
    socket.chmod(0o700)
    # Never inherit a user's DB service, password, SSL options or search path.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment["LC_ALL"] = "C"
    attempted_start = False
    qualified = False
    cleanup_error: str | None = None

    def run(name: str, arguments: list[str], log: str) -> subprocess.CompletedProcess:
        with (output / log).open("wb") as stream:
            return subprocess.run([str(binaries / name), *arguments], env=environment, stdin=subprocess.DEVNULL,
                                  stdout=stream, stderr=subprocess.STDOUT, timeout=120, check=False)

    try:
        initialized = run("initdb", ["-D", str(data), "--encoding=UTF8", "--no-locale", "--auth=trust"], "init.log")
        if initialized.returncode:
            raise RuntimeError("Isolated initdb failed; see init.log")
        attempted_start = True
        started = run("pg_ctl", ["-D", str(data), "-l", str(output / "server.log"), "-o",
                                 f"-c listen_addresses='' -k {socket} -p 65439", "-w", "start"], "start.log")
        if started.returncode:
            raise RuntimeError("Isolated PostgreSQL startup failed; see start.log")
        sql = ROOT / "next/sql"
        connection=["-X","--no-password","-v","ON_ERROR_STOP=1","-h",str(socket),"-p","65439","-U",getpass.getuser(),"-d","postgres"]
        seeded=run("psql",[*connection,"-f",str(sql/"tests/ism-fixture.sql")],"schema.log")
        if seeded.returncode:raise RuntimeError("Isolated ISM schema setup failed")
        seeded=run("psql",[*connection,"-c","create schema remote_private; revoke all on schema remote_private from public,anon,authenticated; create function remote_private.fixture_only_secret() returns text language sql security definer as $$ select 'private fixture'::text $$;"],"namespace-setup.log")
        if seeded.returncode:raise RuntimeError("Cannot prepare the namespace collision fixture")
        refused=run("psql",[*connection,"-f",str(sql/"001_device_enrollment.sql")],"namespace-refusal.log")
        checked=run("psql",[*connection,"-Atqc","select not has_schema_privilege('authenticated','remote_private','USAGE');"],"namespace-privileges.log")
        if refused.returncode==0 or checked.returncode or (output/"namespace-privileges.log").read_text().strip()!="t":
            raise RuntimeError("A pre-existing private schema was reused or had privileges expanded")
        cleared=run("psql",[*connection,"-c","drop schema remote_private cascade;"],"namespace-clear.log")
        if cleared.returncode:raise RuntimeError("Cannot reset the owned namespace fixture")
        active_client_migration = str(sql/"003_require_active_enrollment_client.sql")
        validated=run("psql",[*connection,"-f",str(sql/"001_device_enrollment.sql"),
                              "-f",str(sql/"tests/enrollment.sql"),"-f",active_client_migration,
                              "-f",active_client_migration,"-f",str(sql/"tests/inactive-enrollment-client.sql")],
                      "validation.log")
        if validated.returncode:
            raise RuntimeError("Isolated enrollment verification failed; see validation.log")
        personal = run("psql", [*connection, "-f", str(sql / "tests/personal-auth-fixture.sql"),
                                "-f", str(sql / "007_personal_remote_accounts.sql"),
                                "-f", str(sql / "tests/personal-accounts.sql")], "personal-accounts.log")
        if personal.returncode:
            raise RuntimeError("Personal Remote account verification failed; see personal-accounts.log")
        qualified = True
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}")
    finally:
        if attempted_start:
            try:
                status = run("pg_ctl", ["-D", str(data), "status"], "status.log")
                if status.returncode == 0:
                    stopped = run("pg_ctl", ["-D", str(data), "-m", "fast", "-w", "stop"], "stop.log")
                    if stopped.returncode:
                        cleanup_error = "Owned PostgreSQL server did not stop; retaining its private socket"
                elif status.returncode != 3:
                    cleanup_error = "Owned PostgreSQL state is uncertain; retaining its private socket"
            except (OSError, subprocess.SubprocessError):
                cleanup_error = "Owned PostgreSQL cleanup failed; retaining its private socket"
        if cleanup_error is None:
            shutil.rmtree(socket)
        receipt = {"fixtureQualified": qualified and cleanup_error is None, "tcpEnabled": False,
                   "productionTouched": False, "namespaceCollisionDenied":qualified, "data": str(data), "socketRemoved": not socket.exists(),
                   "cleanupError": cleanup_error}
        (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if cleanup_error:
            print(f"FAIL: {cleanup_error}")
    if qualified and cleanup_error is None:
        print("PASS: isolated enrollment SQL assertions and private-namespace refusal passed; owned server stopped; private socket removed; no production database touched")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
