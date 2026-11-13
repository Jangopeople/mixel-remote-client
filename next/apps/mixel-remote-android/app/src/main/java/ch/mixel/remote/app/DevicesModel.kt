package ch.mixel.remote.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ch.mixel.remote.engine.args
import ch.mixel.remote.engine.message
import ch.mixel.remote.engine.perform
import ch.mixel.remote.engine.run
import ch.mixel.remote.model.DeployLinkCreated
import ch.mixel.remote.model.Device
import ch.mixel.remote.model.DevicePage
import ch.mixel.remote.model.RemoteStatus
import ch.mixel.remote.model.SaasDevice
import ch.mixel.remote.model.SaasGroup
import ch.mixel.remote.model.SaasPresence
import ch.mixel.remote.model.Tenant
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * The devices of the signed-in account: a company's devices (with groups and the connection
 * service's last record), or the devices a Mixel team member or a personal account may reach. Says
 * for every device whether it can be reached, and why not.
 */
class DevicesModel(private val app: AppModel) {
    var devices by mutableStateOf<List<Device>>(emptyList())
        private set
    var loading by mutableStateOf(false)
        private set
    var search by mutableStateOf("")
    var groups by mutableStateOf<List<SaasGroup>>(emptyList())
        private set
    var page by mutableIntStateOf(0)
        private set
    var hasNext by mutableStateOf(false)
        private set
    var message by mutableStateOf("")
        private set
    var error by mutableStateOf("")
    var serverPresence by mutableStateOf<Map<Long, SaasPresence>>(emptyMap())
        private set
    private var checked by mutableStateOf<Set<Long>>(emptySet())
    private var cursors = mutableListOf<Long?>(null)
    private var nextCursor: Long? = null
    private var companyAll: List<Device> = emptyList()
    private var lastPresence = 0L

    val tenant: Tenant? get() = app.company.tenant
    val role: String get() = app.company.overview?.role ?: tenant?.role ?: ""
    val manages: Boolean get() = tenant != null && role in setOf("owner", "admin", "technician")
    val deploys: Boolean get() = tenant != null && role in setOf("owner", "admin")
    val groupsOn: Boolean get() = app.company.overview?.features?.get("groups") ?: false
    val writable: Boolean get() = (app.company.overview?.status ?: tenant?.status ?: "") in setOf("active", "trial", "past_due")
    val room: Int
        get() {
            val overview = app.company.overview ?: return Int.MAX_VALUE
            val limit = overview.limits?.devices ?: return Int.MAX_VALUE
            val usage = overview.usage ?: return Int.MAX_VALUE
            return limit - usage.devices
        }

    /** Why adding this phone to the company is not possible right now ("" when it is). */
    val addBlocked: String
        get() = when {
            !writable -> L("This company is read-only")
            room <= 0 -> L("Device limit of your plan reached")
            else -> ""
        }

    fun reset() {
        devices = emptyList(); companyAll = emptyList(); groups = emptyList()
        page = 0; hasNext = false; cursors = mutableListOf(null); nextCursor = null
        checked = emptySet(); serverPresence = emptyMap(); message = ""; error = ""
    }

    suspend fun load(wanted: Int = 0) {
        if (!app.signedIn) { reset(); return }
        loading = true
        error = ""
        try {
            val tenant = tenant
            if (tenant != null) {
                if (wanted == 0 || companyAll.isEmpty()) {
                    val rows: List<SaasDevice> = app.engine.run(
                        "saas_devices",
                        args("tenant" to tenant.tenantId, "group" to null, "search" to search.ifEmpty { null }),
                    )
                    companyAll = rows.mapNotNull { it.device }
                    if (manages && groupsOn) groups = runCatching { app.engine.run<List<SaasGroup>>("saas_groups", args("tenant" to tenant.tenantId)) }.getOrDefault(emptyList())
                    loadServerPresence()
                }
                page = wanted.coerceIn(0, maxOf(0, (companyAll.size - 1) / PAGE))
                devices = companyAll.drop(page * PAGE).take(PAGE)
                hasNext = (page + 1) * PAGE < companyAll.size
            } else {
                if (wanted == 0) cursors = mutableListOf(null)
                val cursor = if (wanted < cursors.size) cursors[wanted] else nextCursor
                val result: DevicePage = app.engine.run("remote_devices", args("after" to cursor, "search" to search))
                devices = result.devices
                page = wanted
                nextCursor = result.next
                hasNext = result.next != null
                if (cursors.size <= wanted + 1) cursors.add(result.next) else cursors[wanted + 1] = result.next
            }
            message = if (devices.isEmpty()) "" else L("Page %d · %d devices.", page + 1, devices.size)
            checkPresence(force = true)
        } catch (failure: Exception) {
            error = message(failure)
        } finally {
            loading = false
        }
    }

    fun nextPage() { app.scope.launch { load(page + 1) } }
    fun previousPage() { app.scope.launch { load(maxOf(0, page - 1)) } }
    fun reload() { app.scope.launch { load() } }

    private suspend fun loadServerPresence() {
        val tenant = tenant ?: return
        val rows = runCatching { app.engine.run<List<SaasPresence>>("saas_device_presence", args("tenant" to tenant.tenantId)) }.getOrNull() ?: return
        serverPresence = rows.mapNotNull { row -> row.connectionId?.let { it to row } }.toMap()
    }

    /** Asks the connection service which listed devices are online (every 15 s while connected). */
    suspend fun checkPresence(force: Boolean = false) {
        if (!app.remote.connected || devices.isEmpty() || !(force || app.clock() - lastPresence > 15_000)) return
        lastPresence = app.clock()
        val hosts = devices.map { it.connectionId }.filter { it != app.remote.host }
        if (hosts.isEmpty()) return
        runCatching { app.engine.perform("remote_presence", args("hosts" to hosts.take(100))) }
        // The answer arrives in remote_status.online; the next read shows it.
        delay(600)
        checked = checked + hosts
    }

    fun statusChanged(previous: RemoteStatus, next: RemoteStatus) {
        if (next.connected && !previous.connected) lastPresence = 0
        if (next.connected) app.scope.launch { checkPresence() }
    }

    // What a row shows

    enum class Presence { This, Online, Offline, Checking, Unknown }

    fun presence(device: Device): Presence {
        val connected = app.remote.connected
        if (device.connectionId == app.remote.host) return Presence.This
        if (!connected) {
            serverPresence[device.connectionId]?.let { return if (it.status == "online") Presence.Online else Presence.Offline }
            return Presence.Unknown
        }
        if (device.connectionId in app.remote.online) return Presence.Online
        return if (device.connectionId in checked) Presence.Offline else Presence.Checking
    }

    fun label(presence: Presence): String = when (presence) {
        Presence.This -> if (app.remote.connected) L("This device") else L("This device · not connected")
        Presence.Online -> L("Online")
        Presence.Offline -> L("Offline")
        Presence.Checking -> L("Checking…")
        Presence.Unknown -> L("Status unknown")
    }

    fun reason(device: Device): String = when (presence(device)) {
        Presence.This -> if (app.remote.connected) L("You are using this device.") else L("Reconnecting automatically.")
        Presence.Offline -> {
            val seen = serverPresence[device.connectionId]?.let { Format.lastSeen(it.lastSeen) } ?: ""
            (if (seen.isEmpty()) "" else "$seen. ") + L("Turned off, asleep or without internet. It must be online to connect.")
        }
        Presence.Unknown -> L("Shown once this phone is connected.")
        Presence.Online -> L("Ready. The person there accepts your request, unless unattended access is set up.")
        Presence.Checking -> ""
    }

    /** Why Connect is not possible ("" when it is). */
    fun connectBlocked(device: Device): String = when {
        device.connectionId == app.remote.host -> L("This is the device you are using.")
        app.remote.inSession -> L("End the current session first.")
        presence(device) == Presence.Offline -> L("This device is offline.")
        app.busy -> L("Please wait…")
        !app.signedIn -> L("Sign in first.")
        else -> ""
    }

    fun connect(device: Device) {
        message = L("Connecting to %@…", device.name)
        app.section = Section.Connect
        app.connect(device.connectionId, unattended = app.unattended)
    }

    // Manage (company devices)

    /** The deploy link just created (shown once). */
    var deployLink by mutableStateOf<DeployLinkCreated?>(null)

    suspend fun rename(device: Device, name: String): Boolean {
        val tenant = tenant ?: return false
        return change { app.engine.run<String>("saas_rename_device", args("tenant" to tenant.tenantId, "device" to device.device, "name" to name.trim())) }
    }

    suspend fun move(device: Device, group: String?): Boolean {
        val tenant = tenant ?: return false
        return change { app.engine.perform("saas_move_device", args("tenant" to tenant.tenantId, "device" to device.device, "group" to group)) }
    }

    suspend fun remove(device: Device): Boolean {
        val tenant = tenant ?: return false
        return change { app.engine.perform("saas_remove_device", args("tenant" to tenant.tenantId, "device" to device.device)) }
    }

    suspend fun createGroup(name: String): Boolean {
        val tenant = tenant ?: return false
        return change { app.engine.perform("saas_create_group", args("tenant" to tenant.tenantId, "name" to name.trim())) }
    }

    /** "Add other computers": a deploy link with the command for each system (shown once). */
    suspend fun createDeployLink(label: String, days: Int, computers: Int, group: String?): Boolean {
        val tenant = tenant ?: return false
        return change {
            deployLink = app.engine.run(
                "saas_create_deploy_link",
                args("tenant" to tenant.tenantId, "label" to label.trim(), "days" to days, "computers" to computers, "group" to group),
            )
        }
    }

    private suspend fun change(work: suspend () -> Unit): Boolean {
        error = ""
        return try {
            work()
            load(0)
            true
        } catch (failure: Exception) {
            error = message(failure)
            false
        }
    }

    // Wake-on-LAN: the addresses a computer reported in an earlier session.

    fun rememberMacs(connection: Long, macs: List<String>) {
        app.preferences.set("mixel.remote.macs.$connection", macs.take(4).joinToString(","))
    }

    fun macs(device: Device): List<String> =
        app.preferences.get("mixel.remote.macs.${device.connectionId}")?.split(",")?.filter { it.length == 12 }.orEmpty()

    fun wake(device: Device) {
        val macs = macs(device)
        app.act {
            app.engine.perform("wake_on_lan", args("macs" to macs))
            message = L("Wake signal sent to %@. It shows Online when it has started.", device.name)
        }
    }

    companion object {
        const val PAGE = 50
    }
}
