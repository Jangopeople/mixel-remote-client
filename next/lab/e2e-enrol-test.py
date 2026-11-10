#!/usr/bin/env python3
# Live end-to-end test of staff device enrolment (same calls the app makes), run on the VPS. No password is typed anywhere:
# a dedicated staff test account + a seeded verified authenticator + a real magic-link session.
import json, subprocess, time, hmac, hashlib, base64, struct, urllib.request, urllib.error, sys
env={}
for line in open('/opt/supabase-ism/.env'):
    line=line.strip()
    if '=' in line and not line.startswith('#'): k,v=line.split('=',1); env[k]=v.strip().strip('"')
SR=env['SERVICE_ROLE_KEY']; ANON=env['ANON_KEY']
API='https://ism-api.mixel.ch'; AUTH=API+'/auth/v1'; REST=API+'/rest/v1'
EMAIL='appreview-remote-staff@mixel.ch'; SECRET='MIXELREMOTESTAFFTESTSECRET234567'
R=[]
def ok(name,cond,detail=''): R.append((bool(cond),name)); print(('PASS' if cond else 'FAIL'),'|',name,('' if cond else '  <<< '+str(detail)[:180]))
def http(method,url,token=None,body=None):
    h={'content-type':'application/json','apikey':ANON}
    if token: h['authorization']='Bearer '+token
    data=json.dumps(body).encode() if body is not None else None
    try:
        r=urllib.request.urlopen(urllib.request.Request(url,data=data,method=method,headers=h),timeout=25); return r.status,json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        try: return e.code,json.loads(e.read() or b'null')
        except: return e.code,None
def sql(q): return subprocess.run(['docker','exec','ism-postgres','psql','-U','postgres','-d','ism','-Atc',q],capture_output=True,text=True).stdout.strip()
def claims(tok): return json.loads(base64.urlsafe_b64decode(tok.split('.')[1]+'==='))
def totp(sec,at=None):
    key=base64.b32decode(sec+'='*((-len(sec))%8)); c=int((at or time.time())//30)
    hs=hmac.new(key,struct.pack('>Q',c),hashlib.sha1).digest(); o=hs[-1]&0x0f
    return '%06d'%((struct.unpack('>I',hs[o:o+4])[0]&0x7fffffff)%1000000)
uid=None; dev=None
try:
    for u in sql(f"select id from auth.users where email='{EMAIL}'").splitlines():
        if u: 
            for t in ['auth.mfa_challenges where factor_id in (select id from auth.mfa_factors where user_id=\'%s\')'%u,'auth.mfa_factors where user_id=\'%s\''%u,'auth.identities where user_id=\'%s\''%u,'public.users where auth_user_id=\'%s\''%u,'auth.sessions where user_id=\'%s\''%u,'auth.refresh_tokens where user_id=\'%s\''%u,'auth.users where id=\'%s\''%u]: sql('delete from '+t)
    st,u=http('POST',AUTH+'/admin/users',token=SR,body={'email':EMAIL,'email_confirm':True}); uid=u['id']
    ok('create dedicated staff test account',st in (200,201) and uid)
    sub='remote-staff-test-'+uid.replace('-','')[:16]
    sql("insert into auth.identities(provider_id,user_id,identity_data,provider,created_at,updated_at,id) values('%s','%s','%s'::jsonb,'keycloak',now(),now(),gen_random_uuid())"%(sub,uid,json.dumps({'sub':sub,'iss':'https://auth.mixel.ch/application/o/mixel-ism-user','email':EMAIL,'email_verified':True}).replace("'","''")))
    ok('verified Mixel identity (so it is a real staff-style account)',sql(f"select remote_private.verified_mixel_subject('{uid}')")==sub)
    sql(f"insert into public.users(id,auth_user_id,name,email,role,organization_id,is_active) values('{uid}','{uid}','Remote Staff Test','{EMAIL}','admin',null,true)")
    sql(f"insert into auth.mfa_factors(id,user_id,friendly_name,factor_type,status,secret,created_at,updated_at) values(gen_random_uuid(),'{uid}','Authenticator','totp','verified','{SECRET}',now(),now())")
    fid=sql(f"select id from auth.mfa_factors where user_id='{uid}' and factor_type='totp' limit 1")
    ok('staff admin (no org) + verified authenticator seeded',len(fid)==36 and sql(f"select role from public.users where auth_user_id='{uid}'")=='admin')
    # real aal1 session, no password
    st,link=http('POST',AUTH+'/admin/generate_link',token=SR,body={'type':'magiclink','email':EMAIL})
    st,sess=http('POST',AUTH+'/verify',body={'type':'magiclink','token_hash':link['hashed_token']})
    aal1=sess['access_token']; ok('real session minted without a password (aal1)',claims(aal1).get('aal')=='aal1')

    st,ch=http('POST',AUTH+f'/factors/{fid}/challenge',token=aal1,body={})
    st,v=http('POST',AUTH+f'/factors/{fid}/verify',token=aal1,body={'challenge_id':ch['id'],'code':totp(SECRET)})
    aal2=v['access_token']; ok('session upgraded with authenticator code (aal2)',claims(aal2).get('aal')=='aal2')

    print('\n--- Device settings > Find (what the app lists) ---')
    st,devs=http('GET',REST+'/devices?select=id,name,os&order=id.asc&limit=51&name=ilike.*Mi*',token=aal2)
    names=sorted(d['name'] for d in devs) if isinstance(devs,list) else devs
    print('  listed:',names)
    want=['Michael MacBook Pro','Mixel Mac Mini','Mixel Windows Laptop','Mixel QA Windows 10 VM']
    ok('all four of Michael\'s device records are listed for a staff account',isinstance(devs,list) and all(w in names for w in want),names)
    st,d1=http('GET',REST+'/devices?select=id,name,os&order=id.asc&limit=51&name=ilike.*MacBook*',token=aal2)
    ok('search "MacBook" finds the MacBook record',isinstance(d1,list) and [d['name'] for d in d1]==['Michael MacBook Pro'],d1)
    st,d0=http('GET',REST+'/devices?select=id,name,os&order=id.asc&limit=51',token=aal1)
    ok('before the code (aal1) the list is closed',not (isinstance(d0,list) and d0),(st,d0))

    print('\n--- Enrol (throwaway record, same RPC as the app) ---')
    dev=sql("insert into public.devices(client_id,name,type,os) select id,'ZZ enrol selftest (delete)','workstation','other' from public.clients where name='Mixel IT and Corporate Services GmbH' returning id").splitlines()[0]
    ok('throwaway device record created',len(dev)==36,dev)
    key=subprocess.run("openssl genpkey -algorithm ed25519 | openssl pkey -pubout -outform DER | tail -c 32 | xxd -p -c 64",shell=True,capture_output=True,text=True).stdout.strip()
    ok('fresh ed25519 device key generated',len(key)==64,key)
    def rpc(k):
        h={'content-type':'application/json','apikey':ANON,'authorization':'Bearer '+aal2,'accept':'application/vnd.pgrst.object+json'}
        try:
            r=urllib.request.urlopen(urllib.request.Request(REST+'/rpc/remote_enroll_device',data=json.dumps({'target':dev,'key_material':'\\x'+k}).encode(),method='POST',headers=h),timeout=25); return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
    st,row=rpc(key); print('  enrol ->',st,row)
    good=st==200 and row.get('device_id')==dev and row.get('revoked_at') is None and row.get('public_key')=='\\x'+key
    ok('enrolment accepted and key stored',good,(st,row))
    cid=row.get('connection_id') if isinstance(row,dict) else None
    ok('nine-digit Remote ID issued',isinstance(cid,int) and 100000000<=cid<=999999999,cid)
    st,chk=http('GET',REST+f'/remote_device_enrollments?device_id=eq.{dev}&select=device_id,connection_id,generation,public_key,revoked_at&limit=2',token=aal2)
    ok('app re-check: enrolment row readable and matching',isinstance(chk,list) and len(chk)==1 and chk[0]['connection_id']==cid and chk[0]['generation']>=1 and chk[0]['public_key']=='\\x'+key,chk)
    st,again=rpc(key)
    ok('enrolling the same key again is harmless (same Remote ID)',st==200 and again.get('connection_id')==cid,(st,again))
    key2=subprocess.run("openssl genpkey -algorithm ed25519 | openssl pkey -pubout -outform DER | tail -c 32 | xxd -p -c 64",shell=True,capture_output=True,text=True).stdout.strip()
    st,other=rpc(key2); print('  second key ->',st,str(other)[:160])
    ok('a different key cannot take over an enrolled device',st>=400,(st,other))
finally:
    try:
        if dev:
            print('CLEANUP rows:',sql(f"with a as (delete from public.remote_device_enrollments where device_id='{dev}' returning 1), b as (delete from public.devices where id='{dev}' returning 1) select (select count(*) from a)||' enrolment, '||(select count(*) from b)||' device'"))
    except NameError: pass

    if uid:
        for t in ['auth.mfa_challenges where factor_id in (select id from auth.mfa_factors where user_id=\'%s\')'%uid,'auth.mfa_factors where user_id=\'%s\''%uid,'auth.identities where user_id=\'%s\''%uid,'public.users where auth_user_id=\'%s\''%uid,'auth.sessions where user_id=\'%s\''%uid,'auth.refresh_tokens where user_id=\'%s\''%uid,'auth.users where id=\'%s\''%uid]: sql('delete from '+t)
        print('\nCLEANUP:', 'test account fully removed' if sql(f"select count(*) from auth.users where id='{uid}'")=='0' else 'WARNING residual rows')
p=sum(1 for c,_ in R if c); print(f"\n==== {p}/{len(R)} assertions passed ====")
sys.exit(0 if p==len(R) else 1)
