#!/usr/bin/env python3
"""Refresh only the isolated Mac QA relay's private rs.mixel.ch TLS copies."""
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import time

ROOT = Path('/opt/mixel-remote-next-qa')
LINEAGE = Path('/etc/letsencrypt/live/rs.mixel.ch')


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'mixel-vps-01':
        raise RuntimeError('Run only as root on the approved Mixel QA service host')
    os.umask(0o077)
    plan = json.loads((ROOT / 'plan.json').read_text())
    state = json.loads((ROOT / 'state.json').read_text())
    if not state.get('approved') or ROOT.is_symlink():
        raise RuntimeError('Approved private deployment state is required')
    compose = ['docker', 'compose', '-p', 'mixel-remote-next-qa', '-f', str(ROOT / 'compose.json')]
    container = subprocess.check_output(compose + ['ps', '-aq', 'turn'], text=True, stderr=subprocess.PIPE).strip()
    running = False
    if container:
        info = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
        labels = info['Config'].get('Labels', {})
        if labels.get('com.docker.compose.project') != 'mixel-remote-next-qa' or labels.get('com.docker.compose.service') != 'turn' or info['Config']['Image'] != plan['turnImage']:
            raise RuntimeError('Refusing to restart an unrelated relay')
        running = info['State']['Running']
    tls = ROOT / 'private/turn-tls'
    if tls.is_symlink():
        raise RuntimeError('Private relay TLS directory must not be a symlink')
    tls.mkdir(mode=0o750, exist_ok=True)
    os.chown(tls, 0, 10003)
    tls.chmod(0o750)
    pair = tls / ('pair-' + str(time.time_ns()))
    pair.mkdir(mode=0o750)
    os.chown(pair, 0, 10003)
    pair.chmod(0o750)  # The private process umask otherwise removes group traversal.
    for name in ['fullchain.pem', 'privkey.pem']:
        target = pair / name
        target.write_bytes((LINEAGE / name).read_bytes())
        os.chown(target, 0, 10003)
        target.chmod(0o640)
    cert_key = subprocess.check_output(['openssl', 'x509', '-in', str(pair / 'fullchain.pem'), '-pubkey', '-noout'], stderr=subprocess.PIPE)
    private_key = subprocess.check_output(['openssl', 'pkey', '-in', str(pair / 'privkey.pem'), '-pubout'], stderr=subprocess.PIPE)
    if cert_key.strip() != private_key.strip():
        raise RuntimeError('New relay certificate and private key do not match')
    subprocess.run(['openssl', 'x509', '-in', str(pair / 'fullchain.pem'), '-noout', '-checkhost', 'rs.mixel.ch'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    subprocess.run(['openssl', 'x509', '-in', str(pair / 'fullchain.pem'), '-noout', '-checkend', '0'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    temporary = tls / ('.current-' + str(time.time_ns()))
    temporary.symlink_to(pair.name)
    os.replace(temporary, tls / 'current')
    for name in ['fullchain.pem', 'privkey.pem']:
        link = tls / name
        expected = 'current/' + name
        if link.is_symlink():
            if os.readlink(link) != expected:
                raise RuntimeError('Existing TLS link does not belong to this deployment')
        elif link.exists():
            raise RuntimeError('Refusing to replace an unrelated TLS file')
        else:
            link.symlink_to(expected)
    if running:
        subprocess.run(['docker', 'restart', '--time', '10', container], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=30)
        context = ssl.create_default_context()
        for attempt in range(30):
            try:
                with socket.create_connection((plan['publicIp'], 5350), timeout=2) as connection:
                    with context.wrap_socket(connection, server_hostname='rs.mixel.ch'):
                        break
            except (OSError, ssl.SSLError):
                if attempt == 29:
                    raise RuntimeError('Owned QA relay did not expose a trusted TLS listener after refresh') from None
                time.sleep(0.2)
    state['tlsCertificateSha256'] = hashlib.sha256((pair / 'fullchain.pem').read_bytes()).hexdigest()
    state['tlsCopiesRefreshed'] = True
    (ROOT / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
    print('PASS: matching rs.mixel.ch TLS pair copied privately; only an already-running owned QA relay may restart')


if __name__ == '__main__':
    main()
