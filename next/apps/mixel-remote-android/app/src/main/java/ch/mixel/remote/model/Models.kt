package ch.mixel.remote.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// What the engine reports, decoded from its JSON (snake_case keys become camelCase).

/** The sign-in state (`account_status`). */
@Serializable
data class AccountStatus(
    /** `signed_out`, `starting`, `waiting_browser`, `verifying`, `mfa_required`, `profile_setup`, `authenticated`, `error`. */
    val phase: String = "signed_out",
    val message: String = "",
    val account: Account? = null,
    val factors: List<Factor> = emptyList(),
) {
    val signedIn: Boolean get() = phase == "authenticated" && account != null
    val signingIn: Boolean get() = phase in setOf("starting", "waiting_browser", "verifying")
}

@Serializable
data class Account(
    val remembered: Boolean = false,
    val hasCompany: Boolean = false,
    val hasPersonal: Boolean = false,
    val profile: String = "",
    val organization: String? = null,
    val staff: Boolean = false,
    val personal: Boolean = false,
    val displayName: String? = null,
    val companies: List<CompanyAdministration> = emptyList(),
)

@Serializable
data class CompanyAdministration(val companyId: String, val companyName: String)

@Serializable
data class Factor(val id: String, val factorType: String = "totp", val status: String = "verified", val friendlyName: String? = null)

/** The connection to Mixel and the session (`remote_status`). */
@Serializable
data class RemoteStatus(
    /** `offline`, `connecting`, `ready`, `outgoing`, `incoming`, `deciding`, `negotiating`, `active`, `closing`, `error`. */
    val phase: String = "offline",
    val message: String = "",
    val controllerOnly: Boolean = true,
    val platform: String = "android",
    val host: Long? = null,
    val session: String? = null,
    val consent: String? = null,
    val peer: Long? = null,
    val received: Long = 0,
    val sharing: Boolean = false,
    val control: Boolean = false,
    val clipboard: Boolean = false,
    val files: Boolean = false,
    val recordingPermitted: Boolean = false,
    val inputAccess: Boolean = false,
    val unattendedSession: Boolean = false,
    val online: List<Long> = emptyList(),
    val displays: Int = 0,
    val display: Int = 0,
    val displayAll: Boolean = false,
    val hostActions: List<String> = emptyList(),
    val signinId: Long? = null,
    val hostMacs: List<String> = emptyList(),
    val invitations: List<Invitation> = emptyList(),
    val inviteMessage: String? = null,
    val joinedSession: Boolean = false,
    val switched: Boolean = false,
    val peerFeatures: List<String> = emptyList(),
) {
    /** Connected to Mixel (this phone can connect). */
    val connected: Boolean get() = phase in setOf("ready", "active", "incoming", "outgoing", "deciding", "negotiating")
    val disconnected: Boolean get() = phase == "offline" || phase == "error"
    val inSession: Boolean get() = phase in setOf("active", "incoming", "outgoing", "deciding", "negotiating", "closing")
    /** This app views (and maybe controls) another computer right now. */
    val viewing: Boolean get() = phase == "active" && !sharing && session != null
    fun offers(action: String): Boolean = action in hostActions

    companion object {
        val offline = RemoteStatus()
    }
}

@Serializable
data class Invitation(
    val invitation: String,
    val host: Long,
    val inviter: String = "",
    val inviterConnection: Long = 0,
    val control: Boolean = false,
    val unattended: Boolean = false,
    val expiresAt: Long = 0,
)

// Devices

/** A device of the account's directory (`remote_devices`), or of a company (`saas_devices`). */
@Serializable
data class Device(
    val device: String,
    val connectionId: Long,
    val name: String,
    val os: String,
    val groupId: String? = null,
    val groupName: String? = null,
    val generation: Long? = null,
)

@Serializable
data class DevicePage(val devices: List<Device> = emptyList(), val next: Long? = null)

@Serializable
data class SaasDevice(
    val deviceId: String,
    val name: String,
    val os: String,
    val groupId: String? = null,
    val groupName: String? = null,
    val connectionId: Long? = null,
    val generation: Long? = null,
    val revoked: Boolean? = null,
) {
    val device: Device?
        get() = if (connectionId == null || revoked == true) null
        else Device(deviceId, connectionId, name, os, groupId, groupName, generation)
}

@Serializable
data class SaasPresence(val deviceId: String, val connectionId: Long? = null, val status: String, val lastSeen: String? = null)

@Serializable
data class SaasGroup(val groupId: String, val name: String, val devices: Int = 0, val technicians: List<String> = emptyList())

// Company

@Serializable
data class Tenant(
    val tenantId: String,
    val organizationId: String = "",
    val name: String,
    val slug: String = "",
    val role: String,
    val plan: String,
    val status: String,
    val trialEndsAt: String? = null,
)

/** `saas_tenant_overview`: the tenant plus features, limits and usage. */
@Serializable
data class TenantOverview(
    val role: String? = null,
    val status: String? = null,
    val plan: String? = null,
    val trialEndsAt: String? = null,
    val features: Map<String, Boolean>? = null,
    val limits: Limits? = null,
    val usage: Usage? = null,
) {
    @Serializable
    data class Limits(val devices: Int? = null, val technicians: Int? = null)

    @Serializable
    data class Usage(val devices: Int = 0, val technicians: Int = 0)
}

@Serializable
data class Member(val userId: String, val name: String = "", val email: String = "", val role: String, val createdAt: String = "")

@Serializable
data class Invite(
    val id: String,
    val email: String,
    val role: String,
    val createdAt: String = "",
    val expiresAt: String = "",
    val mailState: String? = null,
    val mailSentAt: String? = null,
)

@Serializable
data class SignupResult(val tenantId: String, val plan: String = "", val status: String = "", val trialEndsAt: String? = null)

@Serializable
data class DeployLinkCreated(val token: String, val windowsCommand: String = "", val macosCommand: String = "", val linuxCommand: String = "")

@Serializable
data class SaasEnrollment(val deviceId: String, val connectionId: Long, val generation: Long)

// Session tools

@Serializable
data class ChatMessage(val id: Long, val mine: Boolean, val text: String)

@Serializable
data class FileView(
    val connected: Boolean = false,
    val incoming: Transfer? = null,
    val outgoing: Transfer? = null,
    val message: String = "",
) {
    @Serializable
    data class Transfer(
        val id: String,
        val name: String,
        val size: Long,
        val offset: Long = 0,
        /** `offered`, `waiting`, `transferring`, `paused`, `verifying`, `complete`, `failed`. */
        val phase: String,
        val message: String = "",
    ) {
        val progress: Float get() = if (size <= 0) 0f else (offset.toFloat() / size.toFloat()).coerceIn(0f, 1f)
    }
}

@Serializable
data class StartupSettings(val remembered: Boolean = false, val autostart: Boolean = false, val launchedAtStartup: Boolean = false, val restored: Boolean = false)

@Serializable
data class UpdateInfo(val version: String)

/** `device_self_remove`: removed, queued (sent once online) or stale (the registration changed). */
enum class SelfRemoval { removed, queued, stale }

/** An input event, encoded as the desktop app sends it (`{"kind":"key","usage":4,"down":true}`). */
sealed class InputEvent {
    data class Text(val text: String) : InputEvent()
    data class Key(val usage: Int, val down: Boolean) : InputEvent()
    data class Pointer(val x: Int, val y: Int) : InputEvent()
    data class ButtonClick(val button: Int, val down: Boolean, val clicks: Int) : InputEvent()
    data class Wheel(val horizontal: Int, val vertical: Int) : InputEvent()
    data object ReleaseAll : InputEvent()
    data class SelectDisplay(val index: Int) : InputEvent()
    data class Action(val name: String) : InputEvent()

    fun json(): JsonObject = buildJsonObject {
        when (val event = this@InputEvent) {
            is Text -> { put("kind", "text"); put("text", event.text) }
            is Key -> { put("kind", "key"); put("usage", event.usage); put("down", event.down) }
            is Pointer -> { put("kind", "pointer"); put("x", event.x); put("y", event.y) }
            is ButtonClick -> { put("kind", "button_click"); put("button", event.button); put("down", event.down); put("clicks", event.clicks) }
            is Wheel -> { put("kind", "wheel"); put("horizontal", event.horizontal); put("vertical", event.vertical) }
            ReleaseAll -> put("kind", "release_all")
            is SelectDisplay -> { put("kind", "select_display"); put("index", event.index) }
            is Action -> { put("kind", "action"); put("name", event.name) }
        }
    }
}
