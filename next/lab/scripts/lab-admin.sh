#!/bin/bash
# lab-admin.sh grant|revoke : temporary owner rights for the lab test subject in the admin console DB
set -e
SUB=$(docker exec ism-postgres psql -U postgres -d ism -Atc "select i.provider_id from auth.identities i join auth.users u on u.id=i.user_id where u.email='appreview-remote-staff@mixel.ch' and i.provider='keycloak'")
P=bbbfb7af-7cb5-400f-b715-10fe24c80120
if [ "$1" = grant ]; then
docker exec -i admin-postgres psql -U mixel_admin -d mixel_admin -v ON_ERROR_STOP=1 -q <<SQL
insert into admin.staff_users(authentik_user_id,email,first_name,last_name,status) values('$SUB','appreview-remote-staff@mixel.ch','Remote Lab','Test (temporary)','active') on conflict (authentik_user_id) do update set status='active';
insert into admin.remote_provider_memberships(provider_id,subject,role) values('$P','$SUB','owner') on conflict (provider_id,subject) do update set role='owner';
SQL
echo "granted owner to ${SUB:0:24}"
else
docker exec admin-postgres psql -U mixel_admin -d mixel_admin -Atc "delete from admin.staff_users where email='appreview-remote-staff@mixel.ch'; select count(*) from admin.remote_provider_memberships m left join admin.staff_users s on s.authentik_user_id=m.subject where s.email is null;"
fi
