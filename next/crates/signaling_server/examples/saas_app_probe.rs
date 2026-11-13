//! LAB: checks the desktop app's company management against the live identity service with the
//! same client code the app uses (IsmAuth saas_*): sign-up, members and invitations, roles, devices
//! (add this computer, rename, groups, remove), deploy links, plan limits, read-only and the signed
//! self-removal of a computer. Needs a plan with three fresh QA accounts (owner, tech, member) and
//! a command that runs lab SQL on the QA tenant only (limits, trial end).
//!   cargo run -p signaling_server --example saas_app_probe -- plan.json "ssh host sudo python3 qa.py sql SUFFIX"
//! The plan holds short-lived test-account bearers; keep it private and delete it afterwards.
use ed25519_dalek::{Signer, SigningKey};
use serde::Deserialize;
use signaling_server::auth::{
    IsmAuth,
    saas::{SaasError, SelfRemoval},
};
use std::{collections::HashMap, process::Command, time::{SystemTime, UNIX_EPOCH}};
use uuid::Uuid;

#[derive(Deserialize)]
struct Plan {
    identity: String,
    anon_key: String,
    suffix: String,
    users: HashMap<String, User>,
}
#[derive(Deserialize)]
struct User {
    email: String,
    token: String,
}

struct Probe {
    failures: u32,
    checks: u32,
}
impl Probe {
    fn check(&mut self, label: &str, ok: bool, detail: impl std::fmt::Debug) {
        self.checks += 1;
        if ok {
            println!("PASS {label}");
        } else {
            self.failures += 1;
            println!("FAIL {label}: {detail:?}");
        }
    }
    fn refused<T: std::fmt::Debug>(&mut self, label: &str, result: Result<T, SaasError>, code: &str) {
        let ok = matches!(&result, Err(error) if error.code.as_deref() == Some(code));
        self.check(label, ok, result.map_err(|error| (error.code, error.message)));
    }
}

fn key() -> SigningKey {
    let mut seed = [0u8; 32];
    seed[..16].copy_from_slice(Uuid::new_v4().as_bytes());
    seed[16..].copy_from_slice(Uuid::new_v4().as_bytes());
    SigningKey::from_bytes(&seed)
}

fn lab_sql(command: &str, query: &str) -> String {
    use std::io::Write;
    let mut child = Command::new("sh")
        .arg("-c")
        .arg(command)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .expect("lab SQL command");
    child.stdin.take().unwrap().write_all(query.as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success(), "lab SQL failed");
    String::from_utf8_lossy(&output.stdout).trim().to_owned()
}

/// Stands in for the mail service (SQL 026) for one QA invitation: stores the hash of a fresh secret
/// and marks the e-mail as sent (QA addresses are never mailed). Returns the secret.
fn mailed(sql: &str, suffix: &str, tenant: Uuid, invite: Uuid) -> String {
    let secret = format!("mri_{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    lab_sql(sql, &format!(
        "update remote_saas.invites i set token_hash = sha256(convert_to('{secret}', 'UTF8')), mail_state = 'sent', mail_sent_at = now(), \
         mail_claim = null, mail_claimed_at = null, mail_sends = mail_sends + 1 from remote_saas.tenants t \
         where t.id = i.tenant_id and t.name = 'QA App Probe {suffix}' and i.tenant_id = '{tenant}' and i.id = '{invite}'"
    ));
    secret
}

async fn accept(plan: &Plan, token: &str, secret: &str) -> Result<Uuid, String> {
    let response = reqwest::Client::new()
        .post(format!("{}/rest/v1/rpc/remote_saas_accept_invite", plan.identity))
        .header("apikey", &plan.anon_key)
        .bearer_auth(token)
        .json(&serde_json::json!({"invite_token": secret}))
        .send()
        .await
        .map_err(|error| error.to_string())?;
    let status = response.status();
    let body = response.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(format!("{status} {body}"));
    }
    serde_json::from_str(&body).map_err(|error| error.to_string())
}

#[tokio::main]
async fn main() {
    let mut args = std::env::args().skip(1);
    let plan: Plan = serde_json::from_slice(&std::fs::read(args.next().expect("plan.json")).unwrap()).unwrap();
    let sql = args.next().expect("lab SQL command");
    let ism = IsmAuth::new(plan.identity.parse().unwrap(), plan.anon_key.clone()).unwrap();
    let owner = &plan.users["owner"].token;
    let tech = &plan.users["tech"].token;
    let member = &plan.users["member"].token;
    let mut p = Probe { failures: 0, checks: 0 };
    let company = format!("QA App Probe {}", plan.suffix);

    // Create a company (the app's "Create a company").
    p.check("a new account has no company", ism.saas_my_tenants(owner).await.is_ok_and(|list| list.is_empty()), ());
    let created = ism.saas_signup(owner, &company).await;
    p.check("sign-up creates a Business trial", created.as_ref().is_ok_and(|c| c.plan == "business" && c.status == "trial"), created.as_ref().map_err(|e| &e.message));
    let tenant = created.expect("tenant").tenant_id;
    p.refused("a second sign-up of the same account is refused", ism.saas_signup(owner, "QA App Probe second").await, "42501");
    let principal = ism.authenticate(owner).await;
    p.check("after sign-up the account is a company member (not staff, not personal)", principal.as_ref().is_ok_and(|x| !x.staff && x.tenant.is_some() && !x.is_personal()), principal.is_ok());
    let tenants = ism.saas_my_tenants(owner).await.unwrap_or_default();
    p.check("the company is listed with role owner", tenants.len() == 1 && tenants[0].role == "owner" && tenants[0].tenant_id == tenant, tenants.len());
    let overview = ism.saas_tenant_overview(owner, tenant).await.unwrap();
    p.check("overview: limits, usage and groups feature", overview["limits"]["devices"] == 2000 && overview["usage"]["members"] == 1 && overview["features"]["groups"] == true, &overview["limits"]);

    // Members and invitations.
    let invite_tech = ism.saas_invite_member(owner, tenant, &plan.users["tech"].email, "technician", Some("de")).await.unwrap();
    let invite_member = ism.saas_invite_member(owner, tenant, &plan.users["member"].email, "member", None).await.unwrap();
    p.check("invitations return an id, never the secret", invite_tech != invite_member, ());
    p.refused("owners are never invited", ism.saas_invite_member(owner, tenant, "x@example.com", "owner", None).await, "22023");
    let pending = ism.saas_invites(owner, tenant).await.unwrap_or_default();
    p.check("open invitations are listed with their e-mail state and language", pending.len() == 2
        && pending.iter().any(|i| i.id == invite_tech && i.mail_locale.as_deref() == Some("de") && i.mail_state.is_some()), pending.len());
    let invite_tech = mailed(&sql, &plan.suffix, tenant, invite_tech);
    let invite_member = mailed(&sql, &plan.suffix, tenant, invite_member);
    p.check("the technician joins", accept(&plan, tech, &invite_tech).await == Ok(tenant), ());
    p.check("the member joins", accept(&plan, member, &invite_member).await == Ok(tenant), ());
    let members = ism.saas_members(owner, tenant).await.unwrap_or_default();
    p.check("owner sees three members", members.len() == 3, members.len());
    let id_of = |role: &str| members.iter().find(|m| m.role == role).map(|m| m.user_id).unwrap();
    let (owner_id, tech_id, member_id) = (id_of("owner"), id_of("technician"), id_of("member"));
    p.check("technicians see the members", ism.saas_members(tech, tenant).await.is_ok_and(|m| m.len() == 3), ());
    p.refused("members cannot list members", ism.saas_members(member, tenant).await, "42501");
    p.refused("technicians cannot invite", ism.saas_invite_member(tech, tenant, "y@example.com", "member", None).await, "42501");
    p.check("owner makes the technician an admin", ism.saas_set_member_role(owner, tenant, tech_id, "admin").await.is_ok_and(|x| x), ());
    p.refused("an admin cannot make owners", ism.saas_set_member_role(tech, tenant, member_id, "owner").await, "42501");
    p.refused("an admin cannot change the owner", ism.saas_set_member_role(tech, tenant, owner_id, "member").await, "42501");
    p.check("back to technician", ism.saas_set_member_role(owner, tenant, tech_id, "technician").await.is_ok_and(|x| x), ());
    p.refused("the last owner cannot leave", ism.saas_remove_member(owner, tenant, owner_id).await, "42501");
    p.refused("the last owner cannot be demoted", ism.saas_set_member_role(owner, tenant, owner_id, "admin").await, "42501");

    // Devices: add this computer, rename, groups, access, deploy links.
    let pc = key();
    let added = ism.saas_add_this_device(tech, tenant, "QA Front desk", "macos", &pc.verifying_key(), None).await;
    p.check("a technician adds this computer", added.as_ref().is_ok_and(|a| a.generation >= 1), added.as_ref().map_err(|e| &e.message));
    let added = added.unwrap();
    // What the app's "Remove this computer" does without a remembered registration: look it up by key.
    let found = ism.device_for_key(tech, &pc.verifying_key()).await.ok();
    p.check("this computer is found by its key", found == Some(added.device_id), found);
    let enrolled = ism.authenticate_device(tech, added.device_id).await;
    p.check("and its registration read", enrolled.as_ref().is_ok_and(|e| e.connection_id == added.connection_id && e.generation == added.generation), enrolled.is_ok());
    let again = ism.saas_add_this_device(tech, tenant, "QA Front desk", "macos", &pc.verifying_key(), None).await;
    p.check("adding the same computer again returns its registration", again.as_ref().is_ok_and(|a| a.device_id == added.device_id && a.connection_id == added.connection_id), ());
    p.refused("members cannot add computers", ism.saas_add_this_device(member, tenant, "QA Member PC", "linux", &key().verifying_key(), None).await, "42501");
    let listed = ism.saas_devices(owner, tenant, None, None).await.unwrap_or_default();
    p.check("the owner sees the device with connection ID and generation", listed.iter().any(|d| d.device_id == added.device_id && d.connection_id == Some(added.connection_id) && d.generation == Some(added.generation)), listed.len());
    p.check("a member sees only devices they added", ism.saas_devices(member, tenant, None, None).await.is_ok_and(|d| d.is_empty()), ());
    p.check("rename", ism.saas_rename_device(tech, tenant, added.device_id, "QA Reception").await.is_ok_and(|n| n == "QA Reception"), ());
    p.refused("members cannot rename", ism.saas_rename_device(member, tenant, added.device_id, "Nope").await, "42501");
    let group = ism.saas_create_group(owner, tenant, "QA Sales").await.unwrap();
    p.refused("technicians cannot move into a group they are not granted", ism.saas_move_device(tech, tenant, added.device_id, Some(group)).await, "42501");
    p.check("the owner moves the device into the group", ism.saas_move_device(owner, tenant, added.device_id, Some(group)).await.is_ok_and(|x| x), ());
    p.check("the technician no longer sees it", ism.saas_devices(tech, tenant, None, None).await.is_ok_and(|d| d.iter().all(|x| x.device_id != added.device_id)), ());
    p.check("access to the group is granted", ism.saas_set_group_access(owner, tenant, group, &[tech_id]).await.is_ok_and(|n| n == 1), ());
    let in_group = ism.saas_devices(tech, tenant, Some(group), None).await.unwrap_or_default();
    p.check("group filter shows it to the technician", in_group.len() == 1 && in_group[0].group_name.as_deref() == Some("QA Sales"), in_group.len());
    p.check("groups are listed", ism.saas_groups(tech, tenant).await.is_ok_and(|g| g.iter().any(|x| x.group_id == group && x.devices == 1)), ());
    p.refused("technicians cannot create deploy links", ism.saas_create_deploy_link(tech, tenant, "QA rollout", 7, 5, None).await, "42501");
    let link = ism.saas_create_deploy_link(owner, tenant, "QA rollout", 7, 5, Some(group)).await;
    p.check("owners create a deploy link", link.as_ref().is_ok_and(|s| s.starts_with("mrt_")), link.as_ref().map_err(|e| &e.message));
    let links = ism.saas_deploy_links(owner, tenant).await.unwrap_or_default();
    p.check("the deploy link is listed", links.len() == 1 && links[0].max_uses == 5, links.len());
    p.check("and can be revoked", ism.saas_revoke_deploy_link(owner, tenant, links[0].id).await.is_ok_and(|x| x), ());

    // Plan limit: room for exactly the devices there are.
    lab_sql(&sql, &format!("update remote_saas.tenants set max_devices_override = 1 where name = 'QA App Probe {}' and id = '{tenant}'", plan.suffix));
    p.refused("device limit: another computer is refused", ism.saas_add_this_device(owner, tenant, "QA Second", "linux", &key().verifying_key(), None).await, "54000");
    p.refused("device limit: no deploy link", ism.saas_create_deploy_link(owner, tenant, "QA full", 7, 1, None).await, "54000");
    lab_sql(&sql, &format!("update remote_saas.tenants set max_devices_override = null, extra_technicians = 0, max_technicians_override = 2 where name = 'QA App Probe {}' and id = '{tenant}'", plan.suffix));
    p.refused("technician limit: no more technician invitations", ism.saas_invite_member(owner, tenant, "z@example.com", "technician", None).await, "54000");
    p.refused("technician limit: no promotion of the member", ism.saas_set_member_role(owner, tenant, member_id, "technician").await, "54000");
    p.check("members can still be invited", ism.saas_invite_member(owner, tenant, "z@example.com", "member", None).await.is_ok(), ());
    lab_sql(&sql, &format!("update remote_saas.tenants set max_technicians_override = null where name = 'QA App Probe {}' and id = '{tenant}'", plan.suffix));

    // A second computer to remove as owner, then read-only.
    let laptop = key();
    let second = ism.saas_add_this_device(owner, tenant, "QA Laptop", "windows", &laptop.verifying_key(), None).await.unwrap();
    lab_sql(&sql, &format!("update remote_saas.tenants set trial_ends_at = now() - interval '1 minute' where name = 'QA App Probe {}' and id = '{tenant}'", plan.suffix));
    let view = ism.saas_tenant_overview(owner, tenant).await.unwrap();
    p.check("after the trial the company is read-only", view["status"] == "read_only", &view["status"]);
    p.refused("read-only: no renaming", ism.saas_rename_device(owner, tenant, second.device_id, "QA Renamed").await, "25006");
    p.refused("read-only: no invitations", ism.saas_invite_member(owner, tenant, "w@example.com", "member", None).await, "25006");
    p.refused("read-only: no new computers", ism.saas_add_this_device(owner, tenant, "QA Third", "linux", &key().verifying_key(), None).await, "25006");
    p.check("read-only: removing a device still works", ism.saas_remove_device(owner, tenant, second.device_id).await.is_ok_and(|x| x), ());
    p.check("the removed device is gone", ism.saas_devices(owner, tenant, None, None).await.is_ok_and(|d| d.iter().all(|x| x.device_id != second.device_id)), ());

    // This computer removes itself, signed with its own key (no account needed).
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    let stale = SelfRemoval::new(added.device_id, added.connection_id, added.generation + 1, now, |m| Ok(pc.sign(m).to_bytes())).unwrap();
    p.check("a removal for another registration changes nothing", ism.device_self_remove(&stale).await.is_ok_and(|x| !x), ());
    let forged = SelfRemoval::new(added.device_id, added.connection_id, added.generation, now, |m| Ok(laptop.sign(m).to_bytes())).unwrap();
    p.refused("a removal signed with another key is refused", ism.device_self_remove(&forged).await, "42501");
    // Signed earlier (offline), sent later.
    let queued = SelfRemoval::new(added.device_id, added.connection_id, added.generation, now - 3600, |m| Ok(pc.sign(m).to_bytes())).unwrap();
    p.check("the saved self-removal is accepted", ism.device_self_remove(&queued).await.is_ok_and(|x| x), ());
    p.check("sending it again is harmless", ism.device_self_remove(&queued).await.is_ok_and(|x| x), ());
    p.check("the computer is gone from the company", ism.saas_devices(owner, tenant, None, None).await.is_ok_and(|d| d.is_empty()), ());
    let audit = lab_sql(&sql, &format!("select count(*) from remote_saas.audit_log a join remote_saas.tenants t on t.id = a.tenant_id where t.name = 'QA App Probe {}' and a.action in ('device.self_remove','device.remove')", plan.suffix));
    p.check("both removals are in the audit log", audit == "2", audit);

    // Leaving and removing members (allowed while read-only).
    p.check("the member leaves", ism.saas_remove_member(member, tenant, member_id).await.is_ok_and(|x| x), ());
    p.check("the owner removes the technician", ism.saas_remove_member(owner, tenant, tech_id).await.is_ok_and(|x| x), ());
    p.check("the technician has no company any more", ism.saas_my_tenants(tech).await.is_ok_and(|t| t.is_empty()), ());

    println!("{} checks, {} failed", p.checks, p.failures);
    std::process::exit(i32::from(p.failures > 0));
}
