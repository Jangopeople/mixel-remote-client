package ch.mixel.remote.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ch.mixel.remote.engine.EngineApi
import ch.mixel.remote.engine.EngineFailure
import ch.mixel.remote.engine.args
import ch.mixel.remote.engine.message
import ch.mixel.remote.engine.perform
import ch.mixel.remote.engine.run
import ch.mixel.remote.model.AccountStatus
import ch.mixel.remote.model.RemoteStatus
import ch.mixel.remote.support.L
import ch.mixel.remote.support.Translator
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** The parts of the app (tabs at the bottom). */
enum class Section { Devices, Connect, Account }

/** What the app keeps between starts outside the engine (preferences; memory in tests). */
interface AppPreferences {
    fun get(key: String): String?
    fun set(key: String, value: String?)

    class Memory : AppPreferences {
        private val values = mutableMapOf<String, String>()
        override fun get(key: String) = values[key]
        override fun set(key: String, value: String?) {
            if (value == null) values.remove(key) else values[key] = value
        }
    }
}

/**
 * The app's state: the account, the connection to Mixel and the parts of the app. Reads the engine
 * a few times a second while something happens, rarely otherwise. Same behaviour as the Apple apps'
 * `AppModel` (shared engine, same commands).
 */
class AppModel(
    val engine: EngineApi,
    val scope: CoroutineScope,
    val preferences: AppPreferences = AppPreferences.Memory(),
    /** The app's own address for the end of the browser sign-in (`ch.mixel.remote://auth/callback`). */
    val appCallback: String? = null,
    val clock: () -> Long = System::currentTimeMillis,
) {
    var section by mutableStateOf(Section.Devices)
    var account by mutableStateOf(AccountStatus(phase = "signed_out", message = "Sign in with your Mixel account"))
        private set
    var remote by mutableStateOf(RemoteStatus.offline)
        private set
    /** The last failure of an action, shown near the action. */
    var error by mutableStateOf("")
    /** Progress of a connection being prepared ("Looking up this ID…"). */
    var progress by mutableStateOf("")
    var busy by mutableStateOf(false)
    var verificationCode by mutableStateOf("")
    /** Sign-in providers the Mixel sign-in service offers now (`keycloak` = Mixel ID, `google`, `azure`, `apple`). */
    var providers by mutableStateOf(listOf("keycloak"))
        private set
    /** Connect without confirmation where the company allows it (unattended access). */
    var unattended by mutableStateOf(false)

    val devices = DevicesModel(this)
    val session = SessionModel(this)
    val company = CompanyModel(this)
    val settings = SettingsModel(this)
    val enrollment = EnrollmentModel(this)
    val draw = DrawModel(this)
    val reports = ReportsModel(this)

    /** Opens a web page (legal texts, Mixel ID) in the browser. */
    var openUrl: (String) -> Unit = {}
    /** Opens the Mixel ID sign-in page (a Custom Tab); defaults to [openUrl]. */
    var openSignIn: ((String) -> Unit)? = null

    /** The person chose "Go offline": do not come back online by itself until they connect again. */
    var wentOffline = false
    private var autoTries = 0
    private var nextAutoTry = 0L
    private var loop: Job? = null
    private var lastAccountRead = 0L
    private var accountKey = ""

    val info get() = engine.info
    val signedIn get() = account.signedIn

    /** Starts reading the engine. Idempotent. */
    fun start() {
        if (loop != null) return
        loop = scope.launch {
            loadProviders()
            refreshAccount()
            settings.flushPendingRemoval()
            while (isActive) {
                tick()
                val fast = remote.inSession || remote.phase in setOf("connecting", "outgoing") || account.signingIn
                delay(if (fast) 250 else 1000)
            }
        }
    }

    fun stop() {
        loop?.cancel()
        loop = null
    }

    suspend fun tick() {
        val every = if (account.signingIn || account.phase == "mfa_required") 1_000 else 10_000
        if (clock() - lastAccountRead >= every) refreshAccount()
        refreshRemote()
    }

    suspend fun refreshAccount() {
        lastAccountRead = clock()
        val next = runCatching { engine.run<AccountStatus>("account_status") }.getOrNull() ?: return
        if (next != account) account = next
        val key = if (next.signedIn) "${next.account?.profile}|${next.account?.organization}|${next.account?.personal}" else ""
        if (key != accountKey) {
            accountKey = key
            accountChanged()
        }
    }

    suspend fun refreshRemote() {
        val next = runCatching { engine.run<RemoteStatus>("remote_status") }.getOrNull() ?: return
        val previous = remote
        if (next != previous) remote = next
        if (next.phase == "ready") { autoTries = 0; nextAutoTry = 0 }
        session.statusChanged(previous, next)
        draw.statusChanged(next)
        reports.statusChanged(next)
        devices.statusChanged(previous, next)
        goOnlineIfNeeded()
    }

    /** A set-up phone goes online by itself once someone is signed in. Not again after "Go offline", with a growing pause. */
    suspend fun goOnlineIfNeeded() {
        if (!signedIn || wentOffline || !remote.disconnected || busy || clock() < nextAutoTry || enrollment.needsSetup) return
        val pauses = longArrayOf(0, 5_000, 15_000, 30_000, 60_000)
        autoTries += 1
        nextAutoTry = clock() + pauses[minOf(autoTries, pauses.size - 1)]
        try {
            engine.perform("remote_register")
        } catch (failure: Exception) {
            enrollment.registerFailed(failure)
        }
    }

    private fun accountChanged() {
        error = ""
        devices.reset()
        company.reset()
        enrollment.reset()
        if (signedIn) {
            scope.launch {
                company.load()
                enrollment.check()
                devices.load()
                settings.load()
            }
        }
    }

    /** Runs an action; its failure shows in [error]. The engine is read again afterwards. */
    fun act(work: suspend () -> Unit) {
        if (busy) return
        busy = true
        error = ""
        scope.launch {
            try {
                work()
            } catch (failure: Exception) {
                error = message(failure)
            }
            busy = false
            refreshRemote()
        }
    }

    // Sign-in

    /** Reads which providers the sign-in service offers (Mixel ID always; the others when switched on there). */
    suspend fun loadProviders() {
        val offered = runCatching { engine.run<List<String>>("account_providers") }.getOrNull() ?: return
        // Order as on the portal: Google, Microsoft, Apple, then Mixel ID.
        val order = listOf("google", "azure", "apple", "keycloak")
        providers = order.filter { it in offered }.ifEmpty { listOf("keycloak") }
    }

    /** Browser sign-in with a provider (Custom Tab, PKCE); the provider's last step returns to [appCallback]. */
    fun signIn(provider: String = "keycloak") = act {
        // No page in lab builds that sign in with a QA session file (the engine answers null).
        val url: String? = engine.run(
            "account_sign_in",
            args("open_browser" to false, "provider" to provider, "app_callback" to appCallback),
        )
        if (url != null) (openSignIn ?: openUrl)(url)
        refreshAccount()
    }

    /** The provider sent the person back to the app (`ch.mixel.remote://auth/callback?…`). */
    fun completeSignIn(address: String) {
        scope.launch {
            try {
                engine.perform("account_callback", args("url" to address))
            } catch (failure: Exception) {
                error = message(failure)
            }
            refreshAccount()
        }
    }

    fun cancelSignIn() = act {
        engine.perform("account_cancel")
        refreshAccount()
    }

    fun verify(factor: String) {
        val code = verificationCode.filter { it in '0'..'9' }
        if (code.length != 6) {
            error = L("Enter the 6-digit code")
            return
        }
        verificationCode = ""
        act {
            engine.perform("account_verify", args("factor" to factor, "code" to code))
            refreshAccount()
        }
    }

    fun signOut() = act {
        runCatching { engine.perform("remote_disconnect") }
        engine.perform("set_stay_signed_in", args("enabled" to false))
        engine.perform("account_cancel")
        refreshAccount()
    }

    /** A new Mixel ID sets up its personal space (the name its own devices show). */
    fun setUpPersonal(name: String) = act {
        engine.perform("account_setup", args("name" to name.trim()))
        refreshAccount()
    }

    fun switchWorkspace(personal: Boolean) = act {
        engine.perform("account_workspace", args("personal" to personal))
        refreshAccount()
    }

    // The connection

    fun goOnline() {
        wentOffline = false
        autoTries = 0
        nextAutoTry = 0
        act { engine.perform("remote_register") }
    }

    fun goOffline() {
        wentOffline = true
        session.clear()
        act { engine.perform("remote_disconnect") }
    }

    /**
     * Connects to a Remote ID: brings this phone online first if needed; for a phone with Mixel
     * Quick Support it claims the phone and waits until it is online, so one tap is enough.
     */
    fun connect(id: Long, password: String? = null, unattended: Boolean = false) {
        if (busy) {
            progress = L("Please wait a moment, the previous step is still running.")
            return
        }
        busy = true
        error = ""
        scope.launch {
            try {
                connectSteps(id, password, unattended)
            } catch (failure: Exception) {
                error = message(failure)
                progress = ""
            }
            busy = false
            refreshRemote()
        }
    }

    private suspend fun status(): RemoteStatus? = runCatching { engine.run<RemoteStatus>("remote_status") }.getOrNull()

    private suspend fun waitFor(seconds: Int, ask: (suspend () -> Unit)? = null, until: (RemoteStatus) -> Boolean): Boolean {
        for (round in 0 until seconds) {
            status()?.let { if (until(it)) return true }
            if (ask != null && round % 2 == 0) ask()
            delay(1_000)
        }
        return false
    }

    suspend fun connectSteps(id: Long, password: String?, unattended: Boolean) {
        wentOffline = false
        if (status()?.disconnected == true) {
            progress = L("Getting this computer ready…")
            engine.perform("remote_register")
            if (!waitFor(20) { it.phase == "ready" }) {
                throw EngineFailure("This computer could not go online. Check the internet connection and try again.")
            }
        }
        progress = L("Looking up this ID…")
        val phone: Boolean = try {
            engine.run("quick_phone_claim", args("host" to id))
        } catch (failure: EngineFailure) {
            if (Regex("MFA|staff|42501").containsMatchIn(failure.text)) {
                throw EngineFailure("Connecting to phones needs your Mixel team account with the second confirmation step. Sign in again under Account, then choose Connect.")
            }
            throw EngineFailure("This phone could not be prepared for the session: ${failure.text}")
        }
        if (phone) {
            progress = L("Phone found. Waiting for it to come online… On an iPhone the person taps “Start sharing my screen”.")
            val online = waitFor(120, ask = { runCatching { engine.perform("remote_presence", args("hosts" to listOf(id))) } }) { id in it.online }
            if (!online) throw EngineFailure("The phone did not come online. Ask the person to keep Mixel Quick Support open, then choose Connect again.")
            progress = L("Connecting… The person allows the session on their phone.")
        }
        status()?.let { state ->
            if (state.phase != "ready") {
                throw EngineFailure(
                    if (state.phase in setOf("active", "incoming", "outgoing")) "End the current session first, then connect again."
                    else "This computer is not ready to connect yet. Please try again in a moment.",
                )
            }
        }
        engine.perform(
            "remote_request",
            args("host" to id, "password" to password?.takeIf { it.isNotEmpty() }, "unattended" to (unattended && !phone)),
        )
        session.lastRequestUnattended = unattended && !phone
        progress = L("Waiting for the other person to accept your request.")
    }

    /** The status line of the connection, as the desktop app words it. */
    val connectionMessage: String
        get() {
            if (session.stopping) return L("Ending the session…")
            return when (remote.phase) {
                "offline" -> if (signedIn) L("Connect this controller to the Mixel network.") else L("Sign in to access managed devices.")
                "ready" -> if (remote.message.isEmpty()) L("Ready for a connection.") else Translator.translate(remote.message)
                "outgoing" -> L("Waiting for the other person to accept your request.")
                "connecting", "deciding", "negotiating" -> L("Connecting securely…")
                "active" -> if (remote.control) L("Connected. Keyboard and mouse control is enabled.") else L("Connected in view-only mode.")
                else -> Translator.translate(remote.message)
            }
        }

    /** Before the app goes away: end sessions, go offline. */
    suspend fun shutdown() {
        stop()
        session.clear()
        runCatching { engine.shutdown() }
    }
}
