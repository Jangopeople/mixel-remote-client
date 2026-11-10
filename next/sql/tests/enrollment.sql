-- ISOLATED FIXTURE ONLY: synthetic users and devices; never run in customer DB.
\set ON_ERROR_STOP on
insert into public.organizations values ('00000000-0000-0000-0000-000000000001',true),('00000000-0000-0000-0000-000000000002',true);
insert into public.clients values ('00000000-0000-0000-0000-000000000011','00000000-0000-0000-0000-000000000001'),('00000000-0000-0000-0000-000000000012','00000000-0000-0000-0000-000000000002');
insert into public.devices values ('00000000-0000-0000-0000-000000000021','00000000-0000-0000-0000-000000000011'),('00000000-0000-0000-0000-000000000022','00000000-0000-0000-0000-000000000012');
insert into public.users values ('00000000-0000-0000-0000-000000000031','00000000-0000-0000-0000-000000000041',null,'agent',true),('00000000-0000-0000-0000-000000000032','00000000-0000-0000-0000-000000000042','00000000-0000-0000-0000-000000000001','user',true),('00000000-0000-0000-0000-000000000033','00000000-0000-0000-0000-000000000043','00000000-0000-0000-0000-000000000002','user',true);
set role authenticated;
set request.jwt.claim.sub='00000000-0000-0000-0000-000000000041';
set request.jwt.claims='{"aal":"aal2"}';
do $$ declare first public.remote_device_enrollments; again public.remote_device_enrollments;
begin
 first := public.remote_enroll_device('00000000-0000-0000-0000-000000000021',decode(repeat('01',32),'hex'));
 again := public.remote_enroll_device('00000000-0000-0000-0000-000000000021',decode(repeat('01',32),'hex'));
 if first.connection_id <> again.connection_id or first.generation <> again.generation or first.connection_id not between 100000000 and 999999999 then raise exception 'unstable ID'; end if;
 begin
  perform public.remote_enroll_device('00000000-0000-0000-0000-000000000022',decode(repeat('01',32),'hex'));
  raise exception 'identity rebound';
 exception when insufficient_privilege then null; end;
 begin
  perform public.remote_enroll_device('00000000-0000-0000-0000-000000000021',decode(repeat('02',32),'hex'));
  raise exception 'implicit rotation';
 exception when insufficient_privilege then null; end;
 begin
  perform public.remote_enroll_device('00000000-0000-0000-0000-000000000022',decode('01','hex'));
  raise exception 'invalid key accepted';
 exception when invalid_parameter_value then null; end;
 raise notice 'PASS: stable nine-digit ID, idempotency, duplicate-key binding denial and no implicit rotation';
end $$;
select public.remote_enroll_device('00000000-0000-0000-0000-000000000022',decode(repeat('02',32),'hex')) is not null as second_device_enrolled;
set request.jwt.claims='{"aal":"aal1"}';
do $$ begin
 begin
  perform public.remote_revoke_device('00000000-0000-0000-0000-000000000021',1);
  raise exception 'MFA bypass';
 exception when insufficient_privilege then null; end;
 raise notice 'PASS: staff requires verified MFA';
end $$;
set request.jwt.claim.sub='00000000-0000-0000-0000-000000000042';
set request.jwt.claims='{"aal":"aal2"}';
do $$ begin
 if (select count(*) from public.remote_device_enrollments) <> 1 then raise exception 'tenant read leaked or missing'; end if;
 if exists(select 1 from public.remote_device_enrollments where device_id='00000000-0000-0000-0000-000000000022') then raise exception 'cross tenant read'; end if;
 begin
  perform public.remote_revoke_device('00000000-0000-0000-0000-000000000021',1);
  raise exception 'customer staff impersonation';
 exception when insufficient_privilege then null; end;
 begin
  update public.remote_device_enrollments set generation=10;
  raise exception 'direct mutation allowed';
 exception when insufficient_privilege then null; end;
 begin
  perform 1 from remote_private.enrollment_audit;
  raise exception 'private audit leaked';
 exception when insufficient_privilege then null; end;
 raise notice 'PASS: real authenticated-role RLS and write/audit boundaries';
end $$;
reset role;
update public.organizations set is_active=false where id='00000000-0000-0000-0000-000000000001';
set role authenticated;
do $$ begin if exists(select 1 from public.remote_device_enrollments) then raise exception 'inactive tenant read allowed'; end if; raise notice 'PASS: organization deactivation'; end $$;
reset role;
update public.organizations set is_active=true;
update public.users set is_active=false where auth_user_id='00000000-0000-0000-0000-000000000042';
set role authenticated;
do $$ begin if exists(select 1 from public.remote_device_enrollments) then raise exception 'inactive profile read allowed'; end if; raise notice 'PASS: profile deactivation'; end $$;
set request.jwt.claim.sub='00000000-0000-0000-0000-000000000041';
do $$ declare old_id integer; rotated public.remote_device_enrollments;
begin
 select connection_id into old_id from public.remote_device_enrollments where device_id='00000000-0000-0000-0000-000000000021';
 perform public.remote_revoke_device('00000000-0000-0000-0000-000000000021',1);
 if exists(select 1 from public.remote_device_enrollments where device_id='00000000-0000-0000-0000-000000000021') then raise exception 'revoked key visible'; end if;
 begin
  perform public.remote_rotate_device('00000000-0000-0000-0000-000000000021',1,decode(repeat('03',32),'hex'));
  raise exception 'stale mutation';
 exception when serialization_failure then null; end;
 rotated:=public.remote_rotate_device('00000000-0000-0000-0000-000000000021',2,decode(repeat('03',32),'hex'));
 if rotated.connection_id<>old_id or rotated.generation<>3 or rotated.revoked_at is not null then raise exception 'bad rotation'; end if;
 raise notice 'PASS: revocation, generation conflict and explicit rotation retain public ID';
end $$;
reset role;
do $$ begin
 if (select count(*) from remote_private.enrollment_audit)<>4 then raise exception 'missing or duplicate audit entries'; end if;
 raise notice 'PASS: complete enrollment/revocation/rotation audit';
end $$;
