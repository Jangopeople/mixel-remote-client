-- Mixel Remote 2.2.0: the owner's rollout decision (decision 2, 18 Nov 2026): terminal, task manager and the script library are
-- switched ON for the Mixel company itself; every other company keeps all switches OFF until its administrator enables them.
-- Elevated terminal: admins only. Task manager changes when nobody is at the computer: allowed (decision 4 default).
-- Script approval: four-eyes stays ON (decision 3). Skipped where the Mixel company does not exist (scratch and test databases).
-- Run in one transaction: psql -1 -v ON_ERROR_STOP=1 -f 033_remote_tools_mixel_on.sql
set local lock_timeout = '5s';

insert into remote_private.tool_policies(client_id, terminal, terminal_elevate, tasks, tasks_unattended, scripts, scripts_four_eyes,
                                         updated_by_label)
select c.id, true, 'admins', true, true, true, true, 'migration:033 owner decision 2 (2026-11-18)'
from public.clients c
join remote_private.operator_organization op on op.organization_id = c.organization_id
where c.id = '59f417d9-9bcb-4f14-bc7c-25b1e88112da'
on conflict (client_id) do update set terminal = true, terminal_elevate = 'admins', tasks = true, tasks_unattended = true,
    scripts = true, updated_by_label = excluded.updated_by_label, updated_at = now();

-- The policy change is part of the audit trail like any other.
insert into remote_private.tool_audit(client_id, kind, source, summary, result, detail)
select p.client_id, 'policy.set', 'server', 'Remote tools switched on for the Mixel company (owner decision 2)', 'ok',
       jsonb_build_object('migration', '033', 'terminal', true, 'terminal_elevate', 'admins', 'tasks', true, 'scripts', true)
from remote_private.tool_policies p where p.updated_by_label like 'migration:033%';

