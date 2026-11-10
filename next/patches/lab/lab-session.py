#!/usr/bin/env python3
# LAB: (re)creates the dedicated staff test account and mints N aal2 sessions into files. Run on the VPS with sudo.
#   lab-session.py mint <n> <outdir>   |   lab-session.py remove
import json, subprocess, time, hmac, hashlib, base64, struct, urllib.request, urllib.error, sys, os
env={}
for line in open('/opt/supabase-ism/.env'):
    line=line.strip()
    if '=' in line and not line.startswith('#'): k,v=line.split('=',1); env[k]=v.strip().strip('"')
SR=env['SERVICE_ROLE_KEY']; ANON=env['ANON_KEY']
API='https://ism-api.mixel.ch'; AUTH=API+'/auth/v1'
EMAIL='appreview-remote-staff@mixel.ch'; SECRET='MIXELREMOTESTAFFTESTSECRET234567'; PID='f05eb508-1740-49b1-a162-a804c8a82023'
def http(method,url,token=None,body=None):
    h={'content-type':'application/json','apikey':ANON}
    if token: h['authorization']='Bearer '+token
    data=json.dumps(body).encode() if body is not None else None
    try:
        r=urllib.request.urlopen(urllib.request.Request(url,data=data,method=method,headers=h),timeout=25); return r.status,json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        try: return e.code,json.loads(e.read() or b'null')
        except: return e.code,None
def sql(q):
    r=subprocess.run(['docker','exec','ism-postgres','psql','-U','postgres','-d','ism','-v','ON_ERROR_STOP=1','-Atc',q],capture_output=True,text=True)
    if r.returncode: raise SystemExit('SQL failed: '+r.stderr[:300])
    return r.stdout.strip()
def totp(sec,at=None):
    key=base64.b32decode(sec+'='*((-len(sec))%8)); c=int((at or time.time())//30)
    hs=hmac.new(key,struct.pack('>Q',c),hashlib.sha1).digest(); o=hs[-1]&0x0f
    return '%06d'%((struct.unpack('>I',hs[o:o+4])[0]&0x7fffffff)%1000000)
def remove_auth():
    for u in sql(f"select id from auth.users where email='{EMAIL}'").splitlines():
        for t in ["auth.mfa_challenges where factor_id in (select id from auth.mfa_factors where user_id='%s')","auth.mfa_factors where user_id='%s'","auth.identities where user_id='%s'","auth.sessions where user_id='%s'","auth.refresh_tokens where user_id='%s'"]: sql('delete from '+t%u)
        sql(f"update public.users set is_active=false, auth_user_id=id where auth_user_id='{u}'")
        sql(f"delete from auth.users where id='{u}'")
if sys.argv[1]=='remove':
    remove_auth(); sql(f"update public.users set is_active=false where id='{PID}'"); print('login removed; profile disabled:',sql(f"select is_active from public.users where id='{PID}'")); sys.exit()
n=int(sys.argv[2]); out=sys.argv[3]; os.makedirs(out,exist_ok=True)
uid=sql(f"select id from auth.users where email='{EMAIL}'")
if not uid:
    st,u=http('POST',AUTH+'/admin/users',token=SR,body={'email':EMAIL,'email_confirm':True}); uid=u['id']
    sub='remote-staff-test-'+uid.replace('-','')[:16]
    sql("insert into auth.identities(provider_id,user_id,identity_data,provider,created_at,updated_at,id) values('%s','%s','%s'::jsonb,'keycloak',now(),now(),gen_random_uuid())"%(sub,uid,json.dumps({'sub':sub,'iss':'https://auth.mixel.ch/application/o/mixel-ism-user','email':EMAIL,'email_verified':True})))
    sql(f"insert into auth.mfa_factors(id,user_id,friendly_name,factor_type,status,secret,created_at,updated_at) values(gen_random_uuid(),'{uid}','Authenticator','totp','verified','{SECRET}',now(),now())")
# reuse the one lab actor profile (audit rows reference it), pointed at the fresh login
sql(f"update public.users set auth_user_id='{uid}', is_active=true, name='Remote Lab Staff (test)', role='admin', organization_id=null where id='{PID}'")
fid=sql(f"select id from auth.mfa_factors where user_id='{uid}' and factor_type='totp' limit 1")
for i in range(n):
    st,link=http('POST',AUTH+'/admin/generate_link',token=SR,body={'type':'magiclink','email':EMAIL})
    st,sess=http('POST',AUTH+'/verify',body={'type':'magiclink','token_hash':link['hashed_token']})
    st,ch=http('POST',AUTH+f'/factors/{fid}/challenge',token=sess['access_token'],body={})
    st,v=http('POST',AUTH+f'/factors/{fid}/verify',token=sess['access_token'],body={'challenge_id':ch['id'],'code':totp(SECRET)})
    assert st==200 and v.get('access_token'),(st,v)
    c=json.loads(base64.urlsafe_b64decode(v['access_token'].split('.')[1]+'==='))
    p=os.path.join(out,f'session-{i+1}.json'); fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    os.write(fd,json.dumps({'access_token':v['access_token'],'refresh_token':v['refresh_token'],'expires_in':v['expires_in']}).encode()); os.close(fd)
    print(p,'aal=',c.get('aal'),'expires_in=',v['expires_in'])
