package ch.mixel.remote.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ch.mixel.remote.engine.args
import ch.mixel.remote.engine.message
import ch.mixel.remote.engine.perform
import ch.mixel.remote.engine.run
import ch.mixel.remote.model.Invite
import ch.mixel.remote.model.Member
import ch.mixel.remote.model.SignupResult
import ch.mixel.remote.model.Tenant
import ch.mixel.remote.model.TenantOverview
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L
import ch.mixel.remote.support.Translator

/**
 * The signed-in person's company (Mixel Remote SaaS tenant): plan, trial and usage, members and
 * their roles, invitations, renaming, creating a company, deleting the company or one's account.
 */
class CompanyModel(private val app: AppModel) {
    var tenants by mutableStateOf<List<Tenant>>(emptyList())
        private set
    var overview by mutableStateOf<TenantOverview?>(null)
        private set
    var members by mutableStateOf<List<Member>>(emptyList())
        private set
    var invites by mutableStateOf<List<Invite>>(emptyList())
        private set
    var loaded by mutableStateOf(false)
        private set
    var error by mutableStateOf("")
    var notice by mutableStateOf("")
    var working by mutableStateOf(false)
        private set

    val tenant: Tenant? get() = tenants.firstOrNull()
    val role: String get() = overview?.role ?: tenant?.role ?: ""
    val administers: Boolean get() = role in setOf("owner", "admin")
    val isOwner: Boolean get() = role == "owner"
    val readOnly: Boolean get() = (overview?.status ?: tenant?.status ?: "active") !in setOf("active", "trial", "past_due")

    fun reset() {
        tenants = emptyList(); overview = null; members = emptyList(); invites = emptyList()
        loaded = false; error = ""; notice = ""
    }

    suspend fun load() {
        if (!app.signedIn) { reset(); return }
        tenants = try {
            app.engine.run("saas_my_tenants")
        } catch (_: Exception) {
            // Mixel team accounts, ISM companies and personal spaces have no Remote company.
            emptyList()
        }
        loadOverview()
        loadPeople()
        loaded = true
    }

    suspend fun loadOverview() {
        val tenant = tenant ?: run { overview = null; return }
        overview = runCatching { app.engine.run<TenantOverview>("saas_tenant_overview", args("tenant" to tenant.tenantId)) }.getOrNull()
    }

    suspend fun loadPeople() {
        val tenant = tenant ?: return
        members = runCatching { app.engine.run<List<Member>>("saas_members", args("tenant" to tenant.tenantId)) }.getOrDefault(emptyList())
        invites = if (administers) runCatching { app.engine.run<List<Invite>>("saas_invites", args("tenant" to tenant.tenantId)) }.getOrDefault(emptyList()) else emptyList()
    }

    /** "Business trial until 27 Nov 2026" and similar. */
    val planLine: String
        get() {
            val tenant = tenant ?: return ""
            val plan = Format.plan(overview?.plan ?: tenant.plan)
            return when (overview?.status ?: tenant.status) {
                "trial" -> L("%@ trial until %@", plan, Format.day(overview?.trialEndsAt ?: tenant.trialEndsAt))
                "read_only", "expired", "canceled" -> L("%@ · read-only", plan)
                else -> plan
            }
        }

    val usageLine: String
        get() {
            val usage = overview?.usage ?: return ""
            val devices = overview?.limits?.devices?.let { L("%d of %d devices", Format.number(usage.devices), Format.number(it)) }
                ?: L("%d devices", Format.number(usage.devices))
            val technicians = overview?.limits?.technicians?.let { L("%d of %d technicians", Format.number(usage.technicians), Format.number(it)) }
                ?: L("%d technicians", Format.number(usage.technicians))
            return "$devices · $technicians"
        }

    suspend fun invite(email: String, role: String): Boolean {
        val tenant = tenant ?: return false
        val address = email.trim()
        if (!address.contains("@") || address.length > 254) {
            error = L("Enter an e-mail address")
            return false
        }
        return change {
            app.engine.run<String>("saas_invite_member", args("tenant" to tenant.tenantId, "email" to address, "role" to role, "locale" to Translator.language))
            notice = L("Invitation sent to %@.", address)
        }
    }

    suspend fun resend(invite: Invite) {
        val tenant = tenant ?: return
        change {
            app.engine.perform("saas_resend_invite", args("tenant" to tenant.tenantId, "invite" to invite.id, "locale" to Translator.language))
            notice = L("Invitation sent again to %@.", invite.email)
        }
    }

    suspend fun revoke(invite: Invite) {
        val tenant = tenant ?: return
        change { app.engine.perform("saas_revoke_invite", args("tenant" to tenant.tenantId, "invite" to invite.id)) }
    }

    suspend fun setRole(member: Member, role: String) {
        val tenant = tenant ?: return
        change { app.engine.perform("saas_set_member_role", args("tenant" to tenant.tenantId, "member" to member.userId, "role" to role)) }
    }

    /** Removes a member; for oneself: leaves the company. */
    suspend fun remove(member: Member) {
        val tenant = tenant ?: return
        change { app.engine.perform("saas_remove_member", args("tenant" to tenant.tenantId, "member" to member.userId)) }
        if (member.userId == app.account.account?.profile) { app.refreshAccount(); load() }
    }

    suspend fun rename(name: String): Boolean {
        val tenant = tenant ?: return false
        return change { app.engine.run<String>("saas_rename_tenant", args("tenant" to tenant.tenantId, "name" to name)) }
    }

    /** Creates a company with a 14-day Business trial; this account becomes its owner. */
    suspend fun create(name: String, acceptedTerms: Boolean): Boolean {
        val company = name.trim()
        if (!acceptedTerms) { error = L("Accept the Terms of Service and the Data Processing Agreement to create a company."); return false }
        if (company.isEmpty()) { error = L("Enter a company name"); return false }
        val done = change {
            if (!app.remote.disconnected) app.engine.perform("remote_disconnect")
            app.engine.run<SignupResult>("saas_signup", args("company" to company, "terms" to true))
        }
        if (done) { app.refreshAccount(); load() }
        return done
    }

    suspend fun deleteCompany(confirmName: String): Boolean {
        val tenant = tenant ?: return false
        val done = change { app.engine.perform("saas_delete_company", args("tenant" to tenant.tenantId, "name" to confirmName)) }
        if (done) { app.refreshAccount(); load() }
        return done
    }

    suspend fun deleteAccount(confirmEmail: String): Boolean {
        val done = change { app.engine.perform("saas_delete_my_account", args("email" to confirmEmail)) }
        if (done) app.signOut()
        return done
    }

    suspend fun createPersonalSpace(name: String): Boolean {
        val done = change {
            if (!app.remote.disconnected) app.engine.perform("remote_disconnect")
            app.engine.perform("account_create_personal", args("name" to name))
        }
        if (done) app.refreshAccount()
        return done
    }

    private suspend fun change(work: suspend () -> Unit): Boolean {
        error = ""
        notice = ""
        working = true
        try {
            work()
            loadOverview()
            loadPeople()
            return true
        } catch (failure: Exception) {
            error = message(failure)
            return false
        } finally {
            working = false
        }
    }
}
