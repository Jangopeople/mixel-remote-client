// The Mac and Linux install scripts served at https://remote.mixel.ch/install/ (public/install/).
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const dir = join(__dirname, '../../public/install');
const scripts = { linux: join(dir, 'linux.sh'), macos: join(dir, 'macos.sh') };
const run = (file: string, ...args: string[]) => spawnSync('sh', [file, ...args], { encoding: 'utf8', timeout: 10000 });
const TOKEN = 'mrt_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_';

describe('install scripts', () => {
  it('are valid POSIX shell', () => {
    for (const file of Object.values(scripts)) expect(spawnSync('sh', ['-n', file]).status, file).toBe(0);
  });

  it('refuse a missing or malformed token before doing anything', () => {
    for (const file of Object.values(scripts)) {
      for (const args of [[], ['mri_abcdefghijklmnopqrstuvwxyz'], ["mrt_abcdefghijklmnop'; touch /tmp/x"], [TOKEN, TOKEN]]) {
        const result = run(file, ...args);
        expect(result.status, `${file} ${args.join(' ')}`).not.toBe(0);
        expect(result.stderr).toMatch(/^Mixel Remote: /);
      }
    }
  });

  it('need administrator rights to install (and say how)', () => {
    if (process.getuid?.() === 0) return;
    const linux = run(scripts.linux, TOKEN);
    expect(linux.status).not.toBe(0);
    expect(linux.stderr).toMatch(process.platform === 'linux' ? /sudo/ : /sudo|Linux/);
    if (process.platform === 'darwin') {
      const mac = run(scripts.macos, TOKEN);
      expect(mac.status).not.toBe(0);
      expect(mac.stderr).toMatch(/sudo/);
    }
  });

  it('check the release with the Mixel update key the app uses, and Mixel\'s code signature on the Mac', () => {
    const rust = readFileSync(join(__dirname, '../../../../crates/desktop_app/src/self_update.rs'), 'utf8');
    const bytes = [...rust.match(/const UPDATE_KEY: \[u8; 32\] = \[([^\]]+)\]/)![1].matchAll(/0x([0-9a-f]{2})/g)].map((m) => parseInt(m[1], 16));
    expect(bytes).toHaveLength(32);
    const spki = Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), Buffer.from(bytes)]).toString('base64');
    const linux = readFileSync(scripts.linux, 'utf8');
    expect(linux).toContain(spki);
    expect(linux).toContain('openssl pkeyutl -verify');
    expect(linux).toContain('sha256sum');
    expect(linux).toContain('--signin-enroll "$TOKEN"');
    const mac = readFileSync(scripts.macos, 'utf8');
    expect(mac).toContain('certificate leaf[subject.OU] = \\"$TEAM\\"');
    expect(mac).toContain('TEAM="5277F8NDH4"');
    expect(mac).toContain('shasum -a 256');
    expect(mac).toContain('launchctl asuser "$CONSOLE_UID"');
    for (const text of [linux, mac]) expect(text).toContain('MIN_VERSION="0.1.22"');
  });

  it('compare versions numerically (0.1.22 and later accepted)', () => {
    const body = readFileSync(scripts.linux, 'utf8');
    const fn = body.slice(body.indexOf('MIN_VERSION='), body.indexOf('\n}\n', body.indexOf('version_ok()')) + 3);
    for (const [version, ok] of [['0.1.21', false], ['0.1.22', true], ['0.1.100', true], ['0.2.0', true], ['1.0.0', true], ['0.0.99', false]] as const) {
      const result = spawnSync('sh', ['-c', `${fn}\nversion_ok "${version}"`]);
      expect(result.status === 0, version).toBe(ok);
    }
  });
});
