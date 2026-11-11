package ch.mixel.quicksupport

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.DisplayMetrics
import android.view.WindowManager
import org.json.JSONObject

/** The one support connection of this app: state, the host core, and what the screens show. */
object Support : Native.Listener {
    enum class Phase { OFFLINE, CONNECTING, READY, ASKING, NEGOTIATING, ACTIVE }

    interface Screen {
        fun changed()
        fun ask(remoteId: String, staff: Boolean)
        fun notice(text: String)
    }

    private val main = Handler(Looper.getMainLooper())
    private var handle = 0L
    var screen: Screen? = null
    var phase = Phase.OFFLINE
        private set
    var remoteId = ""
        private set
    var message = ""
        private set
    /** Size of the shared picture, fixed for one connection. */
    var width = 0
        private set
    var height = 0
        private set
    /** What the encoder must do when the viewer needs a complete picture. */
    @Volatile var keyframe: (() -> Unit)? = null
    /** Ends the screen capture when a session is over. */
    @Volatile var sessionEnded: (() -> Unit)? = null

    val online get() = phase != Phase.OFFLINE

    private fun preferences(context: Context) = context.getSharedPreferences("support", Context.MODE_PRIVATE)

    private fun core(context: Context): Long {
        if (handle == 0L) {
            val stored = preferences(context).getString("seed", null)?.let { Base64.decode(it, Base64.NO_WRAP) }
            handle = Native.create(stored, this)
            if (stored == null) {
                Native.seed(handle)?.let { preferences(context).edit().putString("seed", Base64.encodeToString(it, Base64.NO_WRAP)).apply() }
            }
        }
        return handle
    }

    /** The picture: the real screen fitted into 1088 x 1920 (or 1920 x 1088), even numbers. */
    private fun measure(context: Context) {
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        (context.getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay.getRealMetrics(metrics)
        val (w, h) = metrics.widthPixels to metrics.heightPixels
        val scale = minOf(1.0, 1088.0 / minOf(w, h), 1920.0 / maxOf(w, h))
        width = maxOf(16, (w * scale).toInt() / 16 * 16)
        height = maxOf(16, (h * scale).toInt() / 16 * 16)
    }

    /** Registers with the technician's code and goes online. Call from a background thread. */
    fun connect(context: Context, code: String): String? {
        return try {
            val core = core(context)
            val name = "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}".trim().take(50).ifEmpty { "Android phone" }
            val enrollment = Native.enroll(core, code, name)
            remoteId = JSONObject(enrollment).getLong("connection_id").toString().chunked(3).joinToString(" ")
            measure(context)
            Native.start(core, enrollment, width, height, ControlService.enabled(context))
            null
        } catch (error: RuntimeException) {
            error.message ?: context.getString(R.string.failed)
        }
    }

    fun decide(accept: Boolean) { if (handle != 0L) Native.decide(handle, accept) }
    fun endSession() { if (handle != 0L) Native.endSession(handle) }
    fun disconnect() { if (handle != 0L) Native.stop(handle) }
    fun push(buffer: java.nio.ByteBuffer, offset: Int, length: Int) { if (handle != 0L) Native.pushFrame(handle, buffer, offset, length) }

    override fun onEvent(json: String) {
        val event = try { JSONObject(json) } catch (_: Exception) { return }
        when (event.optString("type")) {
            "keyframe" -> keyframe?.invoke()
            "pointer", "button", "wheel", "key", "text", "release" -> ControlService.input(event)
            else -> main.post { apply(event) }
        }
    }

    private fun apply(event: JSONObject) {
        when (event.optString("type")) {
            "status" -> {
                phase = when (event.optString("phase")) {
                    "connecting" -> Phase.CONNECTING
                    "ready" -> Phase.READY
                    "asking" -> Phase.ASKING
                    "negotiating" -> Phase.NEGOTIATING
                    "active" -> Phase.ACTIVE
                    else -> Phase.OFFLINE
                }
                message = event.optString("message")
                // Back to waiting (or offline) means no session runs: the screen capture must not either.
                if (phase == Phase.OFFLINE || phase == Phase.READY) sessionEnded?.invoke()
            }
            "request" -> screen?.ask(event.optLong("connection_id").toString().chunked(3).joinToString(" "), event.optBoolean("staff"))
            "started" -> phase = Phase.ACTIVE
            "ended" -> {
                sessionEnded?.invoke()
                ControlService.release()
                event.optString("message").takeIf { it.isNotEmpty() }?.let { screen?.notice(it) }
            }
        }
        screen?.changed()
    }
}
