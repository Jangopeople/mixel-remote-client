package ch.mixel.remote.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ch.mixel.remote.engine.EngineFailure
import ch.mixel.remote.engine.EngineJson
import ch.mixel.remote.engine.args
import ch.mixel.remote.engine.message
import ch.mixel.remote.engine.perform
import ch.mixel.remote.engine.run
import ch.mixel.remote.model.SaasEnrollment
import ch.mixel.remote.model.SelfRemoval
import ch.mixel.remote.support.L
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable

/**
 * Setting up this phone once, so it can connect: add it to the company, enrol it against an
 * existing record (Mixel team), or register it as a personal device.
 */
class EnrollmentModel(private val app: AppModel) {
    /** The engine found no device identity, or the service does not know this phone yet. */
    var needsSetup by mutableStateOf(false)
        private set
    var current by mutableStateOf<Registration?>(null)
        private set
    var name by mutableStateOf("")
    var error by mutableStateOf("")
    var working by mutableStateOf(false)
        private set
    var companies by mutableStateOf<List<EnrollmentCompany>>(emptyList())
        private set
    var records by mutableStateOf<List<InventoryDevice>>(emptyList())
        private set
    var recordSearch by mutableStateOf("")

    @Serializable
    data class Registration(val device: String, val connectionId: Long, val generation: Long, val tenant: String? = null)

    @Serializable
    data class EnrollmentCompany(val clientId: String, val clientName: String)

    @Serializable
    data class InventoryDevice(val id: String, val name: String, val os: String = "")

    @Serializable
    private data class InventoryPage(val devices: List<InventoryDevice> = emptyList(), val next: String? = null)

    @Serializable
    private data class CurrentEnrollment(val device: String, val connectionId: Long, val name: String = "")

    /** The phone's model for the suggested name ("Pixel 8"). */
    var deviceModel: String = "Android"

    init {
        current = app.preferences.get(KEY)?.let { runCatching { EngineJson.decodeFromString<Registration>(it) }.getOrNull() }
    }

    /** How this account sets up this phone. */
    enum class Path { Company, CompanyMember, Team, Personal, None }

    val path: Path
        get() {
            val account = app.account.account ?: return Path.None
            if (account.staff) return Path.Team
            app.company.tenant?.let { return if (it.role in setOf("owner", "admin", "technician")) Path.Company else Path.CompanyMember }
            if (account.personal || account.hasPersonal) return Path.Personal
            return Path.None
        }

    fun reset() {
        needsSetup = false
        error = ""
        companies = emptyList()
        records = emptyList()
    }

    /** Reads the state after sign-in: suggested name, the team's enrolment record. */
    suspend fun check() {
        if (name.isEmpty()) name = runCatching { app.engine.run<String>("this_computer_name", args("device_name" to "Mixel Remote ($deviceModel)")) }.getOrDefault("")
        if (name.isEmpty()) name = "Mixel Remote ($deviceModel)"
        if (app.account.account?.staff == true) {
            runCatching { app.engine.run<CurrentEnrollment?>("enrollment_current") }.getOrNull()?.let {
                remember(it.device, it.connectionId, current?.generation ?: 0, null)
                needsSetup = false
            }
        }
    }

    fun registerFailed(failure: Throwable) {
        val text = (failure as? EngineFailure)?.text ?: failure.message.orEmpty()
        if ("No saved device identity" in text || "not enrolled" in text || "No saved controller identity" in text) {
            needsSetup = true
        } else {
            app.error = message(failure)
        }
    }

    fun remember(device: String, connection: Long, generation: Long, tenant: String?) {
        val value = Registration(device, connection, generation, tenant)
        current = value
        needsSetup = false
        app.preferences.set(KEY, EngineJson.encodeToString(Registration.serializer(), value))
    }

    fun forget() {
        current = null
        app.preferences.set(KEY, null)
    }

    // Paths

    /** "Add this phone" to the company: its device key becomes one of the company's devices. */
    fun addToCompany(group: String? = null) {
        val name = name.trim()
        if (name.isEmpty() || name.length > 80) { error = L("Enter a computer name of up to 60 characters"); return }
        val tenant = app.company.tenant ?: return
        run {
            if (!app.remote.disconnected) app.engine.perform("remote_disconnect")
            val enrolled: SaasEnrollment = app.engine.run("saas_add_this_device", args("tenant" to tenant.tenantId, "name" to name, "group" to group))
            remember(enrolled.deviceId, enrolled.connectionId, enrolled.generation, tenant.tenantId)
            app.goOnline()
            app.company.loadOverview()
            app.devices.load()
        }
    }

    fun registerPersonal() {
        val name = name.trim()
        if (name.isEmpty()) { error = L("Enter a device name"); return }
        run {
            if (!app.remote.disconnected) app.engine.perform("remote_disconnect")
            val id: Long = app.engine.run("register_personal_device", args("name" to name))
            remember("", id, 0, null)
            app.goOnline()
        }
    }

    fun loadTeamRecords() = run {
        companies = app.engine.run("enrollment_companies")
        val page: InventoryPage = app.engine.run("enrollment_devices", args("after" to null, "search" to recordSearch))
        records = page.devices
    }

    /** Mixel team: enrol this phone against an existing record. */
    fun enroll(record: InventoryDevice) = run {
        if (!app.remote.disconnected) app.engine.perform("remote_disconnect")
        val id: Long = app.engine.run("enroll_this_device", args("device" to record.id, "confirmed" to true))
        remember(record.id, id, 0, null)
        app.goOnline()
    }

    /** Mixel team: add a new record for this phone in a company, then enrol it. */
    fun addAndEnroll(company: String) {
        val name = name.trim()
        if (name.isEmpty()) { error = L("Enter a device name"); return }
        run {
            if (!app.remote.disconnected) app.engine.perform("remote_disconnect")
            val device: String = app.engine.run("add_this_device", args("company" to company, "name" to name))
            val id: Long = app.engine.run("enroll_this_device", args("device" to device, "confirmed" to true))
            remember(device, id, 0, null)
            app.goOnline()
        }
    }

    /** Removes this phone from Mixel Remote (signed with its own key; queued while offline). */
    suspend fun removeThisDevice(): SelfRemoval? {
        error = ""
        working = true
        try {
            app.wentOffline = true
            if (!app.remote.disconnected) app.engine.perform("remote_disconnect")
            val saved = current?.takeIf { it.device.isNotEmpty() && it.generation != 0L }
                ?.let { mapOf("device" to it.device, "connection_id" to it.connectionId, "generation" to it.generation) }
            val status: SelfRemoval = app.engine.run("device_self_remove", args("registration" to saved, "confirmed" to true))
            if (status != SelfRemoval.stale) { forget(); needsSetup = true }
            app.wentOffline = false
            app.devices.load()
            return status
        } catch (failure: Exception) {
            app.wentOffline = false
            error = message(failure)
            return null
        } finally {
            working = false
        }
    }

    private fun run(work: suspend () -> Unit) {
        if (working) return
        working = true
        error = ""
        app.scope.launch {
            try {
                work()
            } catch (failure: Exception) {
                error = message(failure)
            }
            working = false
        }
    }

    companion object {
        const val KEY = "mixel.remote.this-device.v1"
    }
}
