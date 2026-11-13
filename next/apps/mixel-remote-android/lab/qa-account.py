#!/usr/bin/env python3
# LAB: a throwaway QA Mixel ID for the Android app's live checks (never a real person). Run on the VPS with sudo.
#   qa-account.py mint <email> <out.json>    create the login (if needed) and write one session file (0600)
#   qa-account.py remove <email>             delete the login and report what is left for that address
# The address must be qa-android-…@example.test (test domains are never mailed).
import json, os, re, subprocess, sys, urllib.request, urllib.error
env = {}
for line in open('/opt/supabase-ism/.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); env[k] = v.strip().strip('"')
SR = env['SERVICE_ROLE_KEY']; ANON = env['ANON_KEY']
AUTH = 'https://ism-api.mixel.ch/auth/v1'

def http(method, url, token=None, body=None):
    h = {'content-type': 'application/json', 'apikey': ANON}
    if token: h['authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode() if body is not None else None
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=h), timeout=25)
        return r.status, json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b'null')
        except Exception: return e.code, None

def sql(q):
    r = subprocess.run(['docker', 'exec', 'ism-postgres', 'psql', '-U', 'postgres', '-d', 'ism', '-v', 'ON_ERROR_STOP=1', '-Atc', q], capture_output=True, text=True)
    if r.returncode: raise SystemExit('SQL failed: ' + r.stderr[:300])
    return r.stdout.strip()

cmd, email = sys.argv[1], sys.argv[2]
if not re.fullmatch(r'qa-android-[a-z0-9-]{4,40}@example\.test', email):
    raise SystemExit('refused: only qa-android-…@example.test addresses')
if cmd == 'mint':
    out = sys.argv[3]
    uid = sql(f"select id from auth.users where email='{email}'")
    if not uid:
        st, u = http('POST', AUTH + '/admin/users', token=SR, body={'email': email, 'email_confirm': True, 'user_metadata': {'name': 'QA Android'}})
        assert st in (200, 201), (st, u); uid = u['id']
        sub = 'qa-android-' + uid.replace('-', '')[:16]
        sql("insert into auth.identities(provider_id,user_id,identity_data,provider,created_at,updated_at,id) values('%s','%s','%s'::jsonb,'keycloak',now(),now(),gen_random_uuid())"
            % (sub, uid, json.dumps({'sub': sub, 'iss': 'https://auth.mixel.ch/application/o/mixel-ism-user', 'email': email, 'email_verified': True, 'name': 'QA Android'})))
    st, link = http('POST', AUTH + '/admin/generate_link', token=SR, body={'type': 'magiclink', 'email': email})
    assert st == 200, (st, link)
    st, sess = http('POST', AUTH + '/verify', body={'type': 'magiclink', 'token_hash': link['hashed_token']})
    assert st == 200 and sess.get('access_token'), (st, sess)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, json.dumps({'access_token': sess['access_token'], 'refresh_token': sess['refresh_token'], 'expires_in': sess['expires_in']}).encode()); os.close(fd)
    print('session for', uid, '->', out)
elif cmd == 'remove':
    for u in sql(f"select id from auth.users where email='{email}'").splitlines():
        for t in ["auth.mfa_factors where user_id='%s'", "auth.identities where user_id='%s'", "auth.sessions where user_id='%s'", "auth.refresh_tokens where user_id='%s'"]:
            sql('delete from ' + t % u)
        sql(f"delete from auth.users where id='{u}'")
    print('auth users left:', sql(f"select count(*) from auth.users where email='{email}'"))
    print('profiles left:', sql(f"select count(*) from public.users where email='{email}'"))
