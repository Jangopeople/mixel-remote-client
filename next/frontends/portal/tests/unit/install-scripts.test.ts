// The Mac and Linux install scripts served at https://remote.mixel.ch/install/ (public/install/).
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const dir = join(__dirname, '../../public/install');
const scripts = { linux: join(dir, 'linux.sh'), macos: join(dir, 'macos.sh') };
const run = (file: string, args: string[] = [], env: Record<string, string> = {}) =>
  spawnSync('sh', [file, ...args], { encoding: 'utf8', timeout: 10000, env: { PATH: process.env.PATH ?? '/usr/bin:/bin', ...env } });
const TOKEN = 'mrt_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_';

/** The script's helpers (die … ARGUMENT_REFUSED) with `body` run after them, without a terminal. */
function helpers(file: string, body: string, env: Record<string, string> = {}) {
  const text = readFileSync(file, 'utf8');
  const start = text.indexOf('die() {');
  const end = text.indexOf('\n', text.indexOf('ARGUMENT_REFUSED=')) + 1;
  // setsid: no controlling terminal, as under a deployment tool (the prompt cannot be reached).
  const shell = process.platform === 'linux' ? ['setsid', ['-w', 'sh', '-c']] as const : ['sh', ['-c']] as const;
  return spawnSync(shell[0], [...shell[1], `set -eu\n${text.slice(start, end)}\n${body}`], {
    encoding: 'utf8', timeout: 10000, env: { PATH: process.env.PATH ?? '/usr/bin:/bin', ...env },
  });
}

describe('install scripts', () => {
  it('are valid POSIX shell', () => {
    for (const file of Object.values(scripts)) expect(spawnSync('sh', ['-n', file]).status, file).toBe(0);
  });

  it('refuse a deploy token on the command line (ps and the shell history would show it), before doing anything', () => {
    for (const file of Object.values(scripts)) {
      for (const args of [[TOKEN], ['--channel', 'stable', TOKEN], ['mri_abcdefghijklmnopqrstuvwxyz']]) {
        const result = run(file, args);
        expect(result.status, `${file} ${args.join(' ')}`).not.toBe(0);
        expect(result.stderr).toMatch(/^Mixel Remote: the deploy token is not passed on the command line/);
        expect(result.stderr).toMatch(/MIXEL_DEPLOY_TOKEN/);
      }
    }
  });

  it('take the token from MIXEL_DEPLOY_TOKEN and remove the variable, so nothing started later inherits it', () => {
    for (const file of Object.values(scripts)) {
      const result = helpers(file, 'read_token; printf "token=%s\\n" "$TOKEN"; env | grep -c MIXEL_DEPLOY_TOKEN || true', { MIXEL_DEPLOY_TOKEN: ` ${TOKEN}\r` });
      expect(result.status, `${file} ${result.stderr}`).toBe(0);
      expect(result.stdout, file).toBe(`token=${TOKEN}\n0\n`);
    }
  });

  it('refuse a missing, malformed or two-line token (grep checks each line on its own)', () => {
    for (const file of Object.values(scripts)) {
      for (const bad of ['mri_abcdefghijklmnopqrstuvwxyz', "mrt_abcdefghijklmnop'; touch /tmp/x", 'mrt_short', `${TOKEN}\nanything else`]) {
        const result = helpers(file, 'read_token; echo accepted', { MIXEL_DEPLOY_TOKEN: bad });
        expect(result.status, `${file} ${JSON.stringify(bad)}`).not.toBe(0);
        expect(result.stdout).not.toMatch(/accepted/);
        expect(result.stderr).toMatch(/^Mixel Remote: the deploy token is missing or invalid/);
      }
      // No variable and no terminal to ask on (a deployment tool without the variable): says how.
      const none = helpers(file, 'read_token; echo accepted');
      expect(none.status, file).not.toBe(0);
      expect(none.stderr, file).toMatch(/^Mixel Remote: no deploy token: run the command in a terminal .*MIXEL_DEPLOY_TOKEN/);
    }
  });

  it('refuse a channel with a second line', () => {
    for (const file of Object.values(scripts)) {
      const channel = run(file, ['--channel', 'stable\n../../elsewhere'], { MIXEL_DEPLOY_TOKEN: TOKEN });
      expect(channel.status, file).not.toBe(0);
      expect(channel.stderr, file).toMatch(/invalid channel/);
    }
  });

  it('ask for the token at a prompt that does not show it, and hand it to the app on standard input only', () => {
    const linux = readFileSync(scripts.linux, 'utf8');
    const mac = readFileSync(scripts.macos, 'utf8');
    for (const text of [linux, mac]) {
      expect(text).toContain('stty -echo </dev/tty');
      expect(text).toContain('IFS= read -r TOKEN </dev/tty');
      expect(text).toContain("trap 'restore_tty; exit 130' INT TERM HUP");
      expect(text).toContain('unset MIXEL_DEPLOY_TOKEN');
      // The only uses of the token after reading it: checking it and piping it to the app.
      const uses = text.split('\n').filter((line) => line.includes('"$TOKEN"') && !line.trim().startsWith('#'));
      for (const line of uses) expect(line).toMatch(/matches "\$TOKEN"|printf '%s\\n' "\$TOKEN" \| |printf '%s' "\$TOKEN" \| tr |\[ -z "\$TOKEN" \]/);
      expect(text).not.toMatch(/--signin-enroll "\$TOKEN"/);
      expect(text).not.toMatch(/sh -s --/);
    }
    expect(linux).toContain('printf \'%s\\n\' "$TOKEN" | "$APP" --signin-enroll ||');
    expect(mac).toContain('printf \'%s\\n\' "$TOKEN" | /bin/launchctl asuser "$CONSOLE_UID" /usr/bin/sudo -u "$CONSOLE_USER" "$PROGRAM" --signin-enroll');
    // Asked only once the checks that need no token passed (administrator rights first).
    expect(linux.indexOf('  read_token\n')).toBeGreaterThan(linux.indexOf('[ "$(id -u)" = 0 ]'));
    expect(mac.indexOf('  read_token\n')).toBeGreaterThan(mac.indexOf('nobody is signed in on this Mac'));
  });

  it('on a terminal, ask for the token without echoing it and give the terminal back, also on Ctrl-C', () => {
    // A pseudo-terminal from Python's pty module (no extra dependency); skipped where python3 is missing.
    if (spawnSync('python3', ['-c', 'import pty, termios']).status !== 0) return;
    const driver = `
import os, pty, sys, time, termios, select, json
text = open(sys.argv[1]).read()
helpers = text[text.index('die() {'):text.index('\\n', text.index('ARGUMENT_REFUSED=')) + 1]
def session(keys):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp('sh', ['sh', '-c', 'set -eu\\n' + helpers + '\\nread_token\\nprintf "GOT=%s\\\\n" "$TOKEN"'])
    out, echo = b'', None
    def drain(seconds):
        nonlocal out
        end = time.time() + seconds
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try: out += os.read(fd, 4096)
                except OSError: return
    drain(0.8)
    echo = bool(termios.tcgetattr(fd)[3] & termios.ECHO)
    os.write(fd, keys)
    drain(0.8)
    _, status = os.waitpid(pid, 0)
    return {'out': out.decode(), 'echo_while_asking': echo, 'echo_after': bool(termios.tcgetattr(fd)[3] & termios.ECHO), 'code': os.waitstatus_to_exitcode(status)}
print(json.dumps([session((' ' + sys.argv[2] + ' \\r').encode()), session(b'\\x03')]))
`;
    for (const file of Object.values(scripts)) {
      const result = spawnSync('python3', ['-I', '-c', driver, file, TOKEN], { encoding: 'utf8', timeout: 15000 });
      expect(result.status, `${file} ${result.stderr}`).toBe(0);
      const [typed, cancelled] = JSON.parse(result.stdout);
      expect(typed.out, file).toMatch(/paste the deploy token and press Enter \(it is not shown\)/);
      expect(typed.echo_while_asking, file).toBe(false);
      expect(typed.out.split('GOT=')[0], file).not.toContain(TOKEN);
      expect(typed.out, file).toContain(`GOT=${TOKEN}`);
      expect(typed.echo_after, file).toBe(true);
      expect(typed.code, file).toBe(0);
      expect(cancelled.echo_while_asking, file).toBe(false);
      expect(cancelled.echo_after, file).toBe(true);
      expect(cancelled.code, file).toBe(130);
    }
  }, 30000);

  it('do nothing when the download is cut off (curl | sh runs whatever arrived)', () => {
    for (const file of Object.values(scripts)) {
      const body = readFileSync(file, 'utf8');
      expect(body.trimEnd().split('\n').at(-1), file).toBe('main "$@"');
      for (const fraction of [0.3, 0.5, 0.7, 0.9, 0.99]) {
        const cut = body.slice(0, Math.floor(body.length * fraction));
        const result = spawnSync('sh', ['-s'], { input: cut, encoding: 'utf8', timeout: 10000, env: { PATH: process.env.PATH ?? '', MIXEL_DEPLOY_TOKEN: TOKEN } });
        expect(`${result.stdout}${result.stderr}`, `${file} cut at ${fraction}`).not.toMatch(/Mixel Remote: /);
      }
    }
  });

  it('need administrator rights to install (and say how), before asking for the token', () => {
    if (process.getuid?.() === 0) return;
    const linux = run(scripts.linux);
    expect(linux.status).not.toBe(0);
    expect(linux.stderr).toMatch(process.platform === 'linux' ? /\| sudo sh\)/ : /sudo|Linux/);
    if (process.platform === 'darwin') {
      const mac = run(scripts.macos);
      expect(mac.status).not.toBe(0);
      expect(mac.stderr).toMatch(/\| sudo sh\)/);
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
    expect(linux).toContain('--signin-enroll ||');
    const mac = readFileSync(scripts.macos, 'utf8');
    expect(mac).toContain('certificate leaf[subject.OU] = \\"$TEAM\\"');
    expect(mac).toContain('TEAM="5277F8NDH4"');
    expect(mac).toContain('shasum -a 256');
    expect(mac).toContain('launchctl asuser "$CONSOLE_UID"');
    for (const text of [linux, mac]) expect(text).toContain('MIN_VERSION="0.1.22"');
    // The Mac manifest is not signed (no Ed25519 in the macOS openssl), so the minimum version is
    // also checked on the code-signed bundle itself: a replayed old release is refused.
    expect(mac).toMatch(/CFBundleShortVersionString[\s\S]*version_ok "\$BUNDLE_VERSION"/);
    expect(mac.indexOf('BUNDLE_VERSION=')).toBeGreaterThan(mac.indexOf('codesign --verify'));
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
