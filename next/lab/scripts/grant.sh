#!/bin/bash
# LAB: pair (as the console does for an always-on device's enrolling technician) and grant unattended access.
#   grant.sh <connection_id> [revoke]
set -e
CID=$1
SUB=$(docker exec ism-postgres psql -U postgres -d ism -Atc "select i.provider_id from auth.identities i join auth.users u on u.id=i.user_id where u.email='appreview-remote-staff@mixel.ch' and i.provider='keycloak'")
read DEV GEN <<<$(docker exec ism-postgres psql -U postgres -d ism -At -F' ' -c "select device_id, generation from public.remote_device_enrollments where connection_id=$CID and revoked_at is null")
P=bbbfb7af-7cb5-400f-b715-10fe24c80120
if [ "$2" = revoke ]; then
  docker exec admin-postgres psql -U mixel_admin -d mixel_admin -Atc "delete from admin.remote_unattended_grants where device_id='$DEV'; delete from admin.remote_device_pairings where device_id='$DEV'"; exit
fi
docker exec admin-postgres psql -U mixel_admin -d mixel_admin -Atc "insert into admin.remote_device_pairings (provider_id, customer_id, device_id, connection_id, requested_generation, requested_by, expires_at, paired_at) values ('$P','$P','$DEV',$CID,$GEN,'$SUB',now()+interval '10 minutes',now()) on conflict (provider_id,customer_id,device_id) do update set paired_at=now(), requested_by=excluded.requested_by, requested_generation=excluded.requested_generation; insert into admin.remote_unattended_grants values('$P','$P','$DEV','$SUB') on conflict do nothing; select 'paired+granted'"
