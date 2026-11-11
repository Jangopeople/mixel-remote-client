-- ISM ticket link: a Mixel team member saves the note of a remote session as an internal
-- note on a ticket, straight from the Mixel Remote app. Internal notes are never sent to
-- the customer. The ticket is found by its number; the note records who wrote it.
begin;

create or replace function public.remote_ticket_note(ticket text, note text, remote_id bigint default null)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare actor uuid; target record; body text;
begin
    actor := remote_private.staff_actor();
    if note is null or char_length(btrim(note)) not between 1 and 8000
        or ticket is null or char_length(btrim(ticket)) not between 3 and 40
        or (remote_id is not null and remote_id not between 100000000 and 999999999) then
        raise exception 'Invalid ticket note' using errcode = '22023';
    end if;
    select t.id, t.ticket_number, t.title into target
    from public.tickets t
    where upper(t.ticket_number) = upper(btrim(ticket))
    limit 1;
    if not found then
        raise exception 'Ticket not found' using errcode = 'P0002';
    end if;
    body := 'Mixel Remote session'
        || case when remote_id is null then '' else ' with computer ' || remote_id::text end
        || E'\n\n' || btrim(note);
    insert into public.ticket_replies (ticket_id, author_id, author_email, content, is_internal, channel)
    select target.id, u.id, u.email, body, true, 'agent'::public.ism_reply_channel
    from public.users u where u.id = actor;
    if not found then
        raise exception 'Ticket note author not found' using errcode = '42501';
    end if;
    return jsonb_build_object('ticket_number', target.ticket_number, 'title', left(coalesce(target.title, ''), 200));
end $$;
revoke all on function public.remote_ticket_note(text, text, bigint) from public, anon;
grant execute on function public.remote_ticket_note(text, text, bigint) to authenticated;

commit;
