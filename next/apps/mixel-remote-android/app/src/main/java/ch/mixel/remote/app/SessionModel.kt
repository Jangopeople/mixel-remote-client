package ch.mixel.remote.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ch.mixel.remote.engine.args
import ch.mixel.remote.engine.message
import ch.mixel.remote.engine.perform
import ch.mixel.remote.engine.run
import ch.mixel.remote.model.ChatMessage
import ch.mixel.remote.model.FileView
import ch.mixel.remote.model.InputEvent
import ch.mixel.remote.model.Invitation
import ch.mixel.remote.model.RemoteStatus
import ch.mixel.remote.support.L
import kotlin.math.roundToInt
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** The phone's own H.264 decoder (MediaCodec onto the screen), set by the session screen. */
interface HardwareDecoder {
    /** Decodes one access unit; `false`: the decoder failed and the engine should decode instead. */
    fun decode(packet: VideoPacket.Compressed): Boolean
    /** The decoded picture size, once known (0 × 0 before). */
    val pictureWidth: Int
    val pictureHeight: Int
    fun reset()
}

/**
 * The session on the technician's side: the remote picture, touch and keyboard, and the session
 * menu (displays, privacy screen, block input, lock, Ctrl+Alt+Del, restart, sound, chat, files, end).
 */
class SessionModel(private val app: AppModel) {
    /** The latest picture the engine decoded (when the phone's decoder is not used). */
    var picture by mutableStateOf<VideoPacket.Picture?>(null)
        private set
    /** The phone's decoder shows pictures on the screen. */
    var hardwarePicture by mutableStateOf(false)
        private set
    var width by mutableIntStateOf(0)
        private set
    var height by mutableIntStateOf(0)
        private set
    var fps by mutableIntStateOf(0)
        private set
    var stopping by mutableStateOf(false)
        private set
    var error by mutableStateOf("")
    var notice by mutableStateOf("")
    var privacy by mutableStateOf(false)
        private set
    var blockInput by mutableStateOf(false)
        private set
    var lockWhenDone by mutableStateOf(false)
        private set
    var sound by mutableStateOf(true)
        private set
    var restartArmed by mutableStateOf(false)
        private set
    var chat by mutableStateOf<List<ChatMessage>>(emptyList())
        private set
    var chatDraft by mutableStateOf("")
    var files by mutableStateOf<FileView?>(null)
        private set
    /** Files received in this session (transfer id → path in the app's folder), to save or open. */
    var received by mutableStateOf<Map<String, String>>(emptyMap())
        private set

    var clipboardResult by mutableStateOf("")
        private set

    /** After "Restart computer": reconnect when the computer is back (up to 10 minutes). */
    private data class Reconnect(
        val host: Long,
        val signin: Long?,
        val unattended: Boolean,
        val until: Long,
        val since: Long,
        val session: String,
        var wentOffline: Boolean = false,
        var lastTry: Long = 0,
        var lastPresence: Long = 0,
    )
    private var reconnect by mutableStateOf<Reconnect?>(null)

    /** Set by the session screen while its surface exists. */
    @Volatile var decoder: HardwareDecoder? = null
    private var decoderFailed = false
    var lastRequestUnattended = false
    private var video: Job? = null
    private var tools: Job? = null
    private var viewingSession: String? = null
    private var toggleSession: String? = null
    private var stoppedConsent: String? = null
    private val shownTimes = ArrayDeque<Long>()
    private val inputQueue = ArrayDeque<InputEvent>()
    private var sending = false

    val remote: RemoteStatus get() = app.remote
    val session: String? get() = remote.session
    val hasPicture: Boolean get() = picture != null || hardwarePicture
    private val locallyStopped: Boolean get() = remote.consent != null && remote.consent == stoppedConsent

    /** The picture is shown and touches and keys go to the other computer. */
    val controllable: Boolean get() = !stopping && remote.viewing && remote.control && hasPicture && !locallyStopped

    /** The session screen covers the app (also while a restarted computer is being reconnected). */
    val shown: Boolean get() = remote.viewing || remote.phase == "outgoing" || hasPicture || reconnecting
    val reconnecting: Boolean get() = reconnect != null && notice.isNotEmpty()

    /** Stop waiting for a restarted computer. */
    fun stopReconnecting() {
        reconnect = null
        notice = ""
    }

    fun statusChanged(previous: RemoteStatus, next: RemoteStatus) {
        if (next.session != toggleSession) {
            toggleSession = next.session
            privacy = false; blockInput = false; lockWhenDone = false; sound = true
            chat = emptyList(); files = null; received = emptyMap(); keysForText = false
            // A new session starts without the last one's messages (a reconnect keeps its notice).
            error = ""; clipboardResult = ""
            if (reconnect == null) notice = ""
        }
        val session = next.session
        if (next.viewing && !stopping && !locallyStopped && session != null) {
            if (viewingSession != session) startVideo(session)
        } else if (viewingSession != null) {
            clear()
        }
        if (next.phase == "active") startTools() else { tools?.cancel(); tools = null }
        if (!next.inSession) stoppedConsent = null
        // Remember the computer's network addresses, so it can be woken from the device list later.
        val peer = next.peer
        if (peer != null && next.hostMacs.isNotEmpty() && next.hostMacs != previous.hostMacs) app.devices.rememberMacs(peer, next.hostMacs)
        continueReconnect(next)
    }

    private fun continueReconnect(status: RemoteStatus) {
        val plan = reconnect ?: return
        if (status.phase == "active") {
            if (status.session != plan.session) { reconnect = null; notice = "" }
            return
        }
        if (status.phase in setOf("negotiating", "outgoing", "deciding")) return
        val now = app.clock()
        if (now > plan.until) {
            reconnect = null
            notice = ""
            error = L("The remote computer did not come back within 10 minutes.")
            return
        }
        if (now - plan.lastPresence > 5_000) {
            plan.lastPresence = now
            val hosts = listOfNotNull(plan.host, plan.signin)
            app.scope.launch { runCatching { app.engine.perform("remote_presence", args("hosts" to hosts)) } }
        }
        if (plan.host !in status.online) plan.wentOffline = true
        val ready = plan.wentOffline || now - plan.since > 120_000
        var target: Long? = null
        if (ready) {
            target = when {
                plan.host in status.online -> plan.host
                plan.signin != null && plan.signin in status.online && now - plan.since > 45_000 -> plan.signin
                plan.unattended && now - plan.since > 60_000 -> plan.host
                else -> null
            }
        }
        notice = L("The remote computer is restarting. Mixel Remote reconnects when it is back…")
        if (target != null && status.phase == "ready" && !app.busy && now - plan.lastTry > 15_000) {
            plan.lastTry = now
            val unattended = plan.unattended || target == plan.signin
            app.act { app.engine.perform("remote_request", args("host" to target, "password" to null, "unattended" to unattended)) }
        }
    }

    /** Text between the clipboards: `push` sends this phone's clipboard, otherwise it fetches the remote one. */
    fun clipboard(push: Boolean) {
        val session = session ?: return
        clipboardResult = L("Transferring…")
        app.scope.launch {
            clipboardResult = try {
                app.engine.perform("remote_clipboard", args("session" to session, "push" to push))
                if (push) L("Remote clipboard updated.") else L("Your clipboard was updated.")
            } catch (failure: Exception) {
                message(failure)
            }
        }
    }

    /** Stops the picture and forgets it. */
    fun clear() {
        video?.cancel()
        video = null
        viewingSession = null
        picture = null
        hardwarePicture = false
        width = 0; height = 0; fps = 0
        decoderFailed = false
        decoder?.reset()
        inputQueue.clear()
    }

    private fun startVideo(session: String) {
        clear()
        viewingSession = session
        video = app.scope.launch {
            var sequence = 0L
            while (isActive) {
                try {
                    val sink = decoder?.takeIf { !decoderFailed }
                    val data = app.engine.nextVideo(session, sink != null)
                    if (viewingSession != session) return@launch
                    if (data.isEmpty()) {
                        // The phone's decoder shows its first picture a moment after the packet.
                        val target = decoder
                        if (target != null && !decoderFailed && target.pictureWidth > 0 &&
                            (!hardwarePicture || width != target.pictureWidth || height != target.pictureHeight)
                        ) shown(target.pictureWidth, target.pictureHeight, hardware = true)
                        delay(16)
                        continue
                    }
                    when (val packet = VideoPacket.decode(data, sequence)) {
                        is VideoPacket.Compressed -> {
                            sequence = packet.sequence
                            val target = decoder
                            if (target == null || decoderFailed) continue
                            if (!target.decode(packet)) {
                                // Ask the engine to decode from now on (it requests a fresh picture).
                                decoderFailed = true
                                hardwarePicture = false
                                continue
                            }
                            if (target.pictureWidth > 0) shown(target.pictureWidth, target.pictureHeight, hardware = true)
                        }
                        is VideoPacket.Picture -> {
                            sequence = packet.sequence
                            picture = packet
                            hardwarePicture = false
                            shown(packet.width, packet.height, hardware = false)
                        }
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (failure: Exception) {
                    if (viewingSession == session) {
                        error = if (failure is VideoPacket.Invalid) L("Invalid native video frame") else message(failure)
                        runCatching { app.engine.perform("remote_close", args("session" to session)) }
                        clear()
                    }
                    return@launch
                }
            }
        }
    }

    private fun shown(pictureWidth: Int, pictureHeight: Int, hardware: Boolean) {
        if (hardware) hardwarePicture = true
        if (width != pictureWidth) width = pictureWidth
        if (height != pictureHeight) height = pictureHeight
        val now = app.clock()
        shownTimes.addLast(now)
        while (shownTimes.isNotEmpty() && now - shownTimes.first() > 1_000) shownTimes.removeFirst()
        if (fps != shownTimes.size) fps = shownTimes.size
    }

    /** For screenshots and tests: show this picture without a running engine. */
    fun showSample(data: ByteArray) {
        val packet = VideoPacket.decode(data) as? VideoPacket.Picture ?: return
        picture = packet
        shown(packet.width, packet.height, hardware = false)
    }

    // Input

    /** Queues an input event; pointer moves replace the previous unsent move. */
    fun send(event: InputEvent) {
        val session = session ?: return
        if (event !is InputEvent.ReleaseAll && !controllable) return
        if (event is InputEvent.Pointer && inputQueue.lastOrNull() is InputEvent.Pointer) {
            inputQueue[inputQueue.size - 1] = event
        } else if (inputQueue.size >= 64) {
            inputQueue.clear()
            error = L("Input delivery stalled; the session has been stopped")
            app.scope.launch { runCatching { app.engine.perform("remote_close", args("session" to session)) } }
            return
        } else {
            inputQueue.addLast(event)
        }
        flush(session)
    }

    private fun flush(session: String) {
        if (sending) return
        sending = true
        app.scope.launch {
            try {
                while (inputQueue.isNotEmpty()) {
                    val event = inputQueue.removeFirst()
                    if (this@SessionModel.session != session) { inputQueue.clear(); return@launch }
                    try {
                        if (event is InputEvent.Text && keysForText) { typeAsKeys(event.text); continue }
                        val posted = app.engine.input(session, event.json().toString())
                        if (!posted && event is InputEvent.Text) {
                            // Some computers (Linux X11) take keys only: type the text as key presses.
                            keysForText = true
                            if (!typeAsKeys(event.text)) error = L("The remote computer could not type this text.")
                        }
                    } catch (failure: Exception) {
                        inputQueue.clear()
                        error = message(failure)
                        return@launch
                    }
                }
            } finally {
                sending = false
            }
        }
    }

    /** The remote computer refused text: send key presses for the rest of this session. */
    private var keysForText = false

    /** Types `text` as US-layout key presses; `false` when a character has no key. */
    private suspend fun typeAsKeys(text: String): Boolean {
        val session = session ?: return false
        var all = true
        for (character in text) {
            val (usage, shift) = ch.mixel.remote.support.KeyMap.press(character) ?: run { all = false; null } ?: continue
            val keys = buildList {
                if (shift) add(InputEvent.Key(ch.mixel.remote.support.KeyMap.SHIFT, true))
                add(InputEvent.Key(usage, true)); add(InputEvent.Key(usage, false))
                if (shift) add(InputEvent.Key(ch.mixel.remote.support.KeyMap.SHIFT, false))
            }
            for (key in keys) app.engine.input(session, key.json().toString())
        }
        return all
    }

    /** Moves the remote pointer to a point of the picture (0…1 in both directions). */
    fun move(x: Float, y: Float) = send(InputEvent.Pointer(normalized(x), normalized(y)))

    /** `button`: 1 left, 2 right, 3 middle (the session protocol's numbers). */
    fun click(x: Float, y: Float, button: Int = 1) {
        move(x, y)
        send(InputEvent.ButtonClick(button, true, 1))
        send(InputEvent.ButtonClick(button, false, 1))
    }

    fun doubleClick(x: Float, y: Float) {
        move(x, y)
        for (clicks in 1..2) {
            send(InputEvent.ButtonClick(1, true, clicks))
            send(InputEvent.ButtonClick(1, false, clicks))
        }
    }

    fun scroll(horizontal: Int, vertical: Int) {
        if (horizontal != 0 || vertical != 0) send(InputEvent.Wheel(horizontal.coerceIn(-120, 120), vertical.coerceIn(-120, 120)))
    }

    fun type(text: String) {
        if (text.isNotEmpty()) send(InputEvent.Text(text))
    }

    /** Presses and releases a key, with modifiers held around it (Ctrl+C and similar). */
    fun press(usage: Int, modifiers: List<Int> = emptyList()) {
        for (modifier in modifiers) send(InputEvent.Key(modifier, true))
        send(InputEvent.Key(usage, true))
        send(InputEvent.Key(usage, false))
        for (modifier in modifiers.reversed()) send(InputEvent.Key(modifier, false))
    }

    fun key(usage: Int, down: Boolean) = send(InputEvent.Key(usage, down))

    // Session menu

    private fun action(name: String, quiet: Boolean = false) {
        val session = session ?: return
        app.scope.launch {
            try {
                app.engine.input(session, InputEvent.Action(name).json().toString())
            } catch (failure: Exception) {
                if (!quiet) error = message(failure)
            }
        }
    }

    fun togglePrivacy() { privacy = !privacy; action("privacy:${if (privacy) "on" else "off"}") }
    fun toggleBlockInput() { blockInput = !blockInput; action("block:${if (blockInput) "on" else "off"}") }
    fun toggleLockWhenDone() { lockWhenDone = !lockWhenDone; action("lockend:${if (lockWhenDone) "on" else "off"}") }
    fun toggleSound() { sound = !sound; action(if (sound) "sound:on" else "sound:off", quiet = true) }
    fun lockNow() = action("lock")
    fun ctrlAltDel() = action("sas")

    fun selectDisplay(index: Int) {
        val session = session ?: return
        app.scope.launch { runCatching { app.engine.input(session, InputEvent.SelectDisplay(index).json().toString()) } }
    }

    /** The first tap arms, the second (within 5 s) restarts the remote computer. */
    fun restartComputer() {
        if (session == null) return
        if (!restartArmed) {
            restartArmed = true
            app.scope.launch { delay(5_000); restartArmed = false }
            return
        }
        restartArmed = false
        val peer = remote.peer ?: return
        val now = app.clock()
        reconnect = Reconnect(peer, remote.signinId, lastRequestUnattended || remote.unattendedSession, now + 600_000, now, session ?: return)
        notice = L("The remote computer is restarting. Mixel Remote reconnects when it is back…")
        action("restart")
    }

    /** Renew the connection (new keys, same session). */
    fun renew() {
        val session = session ?: return
        app.act { app.engine.perform("remote_restart", args("session" to session)) }
    }

    /** Ends the session for both sides. */
    fun end() {
        val consent = remote.consent
        if (consent == null) {
            // Still waiting for the other side: withdraw the request.
            session?.let { id -> app.act { app.engine.perform("remote_close", args("session" to id)) } }
            return
        }
        if (stopping) return
        stoppedConsent = consent
        stopping = true
        clear()
        error = ""
        app.scope.launch {
            try {
                app.engine.perform("remote_stop_session", args("consent" to consent))
            } catch (failure: Exception) {
                error = L("Could not confirm that the session ended. Try End session again.") + " " + message(failure)
            }
            stopping = false
            app.refreshRemote()
        }
    }

    // Chat and files

    private fun startTools() {
        if (tools != null) return
        tools = app.scope.launch {
            while (isActive) {
                readTools()
                delay(700)
            }
        }
    }

    suspend fun readTools() {
        val status = remote
        val session = status.session
        if (session != null && status.phase == "active") {
            runCatching { app.engine.run<List<ChatMessage>?>("remote_chat_messages", args("session" to session)) }.getOrNull()?.let { if (it != chat) chat = it }
        }
        val consent = status.consent
        if (consent != null && status.files && status.phase == "active") {
            val view = runCatching { app.engine.run<FileView?>("remote_file_status", args("consent" to consent)) }.getOrNull()
            if (view != files) files = view
        } else if (files != null) {
            files = null
        }
    }

    fun sendChat() {
        val text = chatDraft.trim()
        val session = session
        if (session == null || text.isEmpty()) return
        chatDraft = ""
        app.scope.launch {
            try {
                app.engine.perform("remote_chat_send", args("session" to session, "text" to text))
                readTools()
            } catch (failure: Exception) {
                error = message(failure)
            }
        }
    }

    /** Sends the file at `path` (a copy the app made of the chosen document). */
    fun sendFile(path: String) {
        val consent = remote.consent ?: return
        app.scope.launch {
            try {
                app.engine.perform("remote_file_send", args("consent" to consent, "path" to path))
                readTools()
            } catch (failure: Exception) {
                error = message(failure)
            }
        }
    }

    /** The name the other side gave a file it offers. */
    suspend fun offeredName(id: String): String? {
        val consent = remote.consent ?: return null
        return runCatching { app.engine.run<String>("remote_file_pending", args("consent" to consent, "id" to id)) }.getOrNull()
    }

    /** Receives an offered file into `path` (the app's folder; the person saves it from there). */
    fun acceptFile(id: String, path: String) {
        val consent = remote.consent ?: return
        app.scope.launch {
            try {
                app.engine.perform("remote_file_accept", args("consent" to consent, "id" to id, "path" to path))
                received = received + (id to path)
                readTools()
            } catch (failure: Exception) {
                error = message(failure)
            }
        }
    }

    fun cancelFile(id: String) {
        val consent = remote.consent ?: return
        app.scope.launch {
            runCatching { app.engine.perform("remote_file_cancel", args("consent" to consent, "id" to id)) }
            readTools()
        }
    }

    // Colleagues

    fun join(invitation: Invitation) = app.act { app.engine.perform("remote_join", args("invitation" to invitation.invitation)) }
    fun dismiss(invitation: Invitation) = app.act { app.engine.perform("remote_dismiss_invitation", args("invitation" to invitation.invitation)) }

    companion object {
        /** A position in the picture (0…1) as the engine expects it (0…65535). */
        fun normalized(value: Float): Int = (value.coerceIn(0f, 1f) * 65535f).roundToInt()
    }
}
