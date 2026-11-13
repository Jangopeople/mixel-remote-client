package ch.mixel.remote.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ch.mixel.remote.engine.perform
import ch.mixel.remote.model.RemoteStatus
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Drawing on the remote screen (pen, highlighter, arrow; five colours, four sizes, undo, clear),
 * as the desktop app does it: the person at the computer sees the strokes; nothing is clicked.
 * Points are 0…65535 of the shared picture and go out about 20 times a second, at most 64 to a
 * message. Hosts that put the drawing into the picture itself (`drawpic`, Linux X11) get finished
 * strokes from the picture after 1.5 s; elsewhere the strokes fade 5 s after the last change.
 */
class DrawModel(private val app: AppModel) {
    enum class Tool(val wire: String) { Pen("pen"), Highlighter("highlighter"), Arrow("arrow") }

    data class Stroke(val id: Int, val tool: Tool, val color: Int, val width: Int, val points: MutableList<Pair<Int, Int>>, var done: Boolean = false, var finished: Long = 0)

    /** Draw mode: one finger draws instead of moving the pointer. */
    var on by mutableStateOf(false)
        private set
    var tool by mutableStateOf(Tool.Pen)
    var color by mutableIntStateOf(0)
    var width by mutableIntStateOf(2)
    /** The strokes this phone shows (its own); [revision] changes with every change. */
    val strokes = mutableListOf<Stroke>()
    var revision by mutableIntStateOf(0)
        private set
    var lastChange by mutableLongStateOf(0)
        private set

    private var active: Stroke? = null
    private var firstSent = false
    private val outbox = mutableListOf<Pair<Int, Int>>()
    private var nextId = 1
    private val queue = ArrayDeque<Pair<String, JsonObject>>()
    private var sending = false
    private var ticker: Job? = null
    private var session: String? = null

    /** The other computer shows drawings (and this session views it). */
    val available: Boolean get() = app.remote.viewing && FEATURE in app.remote.peerFeatures
    val inPicture: Boolean get() = IN_PICTURE in app.remote.peerFeatures

    fun toggle() {
        if (!available) { on = false; return }
        on = !on
        if (!on) finish()
    }

    fun statusChanged(next: RemoteStatus) {
        if (next.session != session || !available) {
            reset()
            session = if (available) next.session else null
        }
    }

    private fun reset() {
        strokes.clear(); active = null; outbox.clear(); queue.clear(); on = false
        ticker?.cancel(); ticker = null
        revision++
    }

    /** A finger touches the picture at x, y (0…65535). */
    fun start(x: Int, y: Int) {
        if (!on || !available) return
        finish()
        val stroke = Stroke(nextId, tool, color, width, mutableListOf(x to y))
        nextId = nextId % 65535 + 1
        strokes.add(stroke)
        if (strokes.size > MAX_STROKES) strokes.removeAt(0)
        active = stroke
        firstSent = false
        outbox.clear()
        outbox.add(x to y)
        changed()
        ensureTicker()
    }

    fun move(x: Int, y: Int, pictureWidth: Int) {
        val stroke = active ?: return
        val last = stroke.points.last()
        if (stroke.tool == Tool.Arrow) {
            // Only the end moves: the other side draws from the first to the last point.
            if (last == x to y) return
            if (stroke.points.size > 1) stroke.points[1] = x to y else stroke.points.add(x to y)
            outbox.clear()
            if (firstSent) outbox.add(x to y) else { outbox.add(stroke.points[0]); outbox.add(x to y) }
        } else {
            // Leave out movements of less than a pixel of the picture.
            val step = 0.75 * 65535 / maxOf(1, pictureWidth)
            if (kotlin.math.abs(last.first - x) < step && kotlin.math.abs(last.second - y) < step) return
            if (stroke.points.size >= MAX_STROKE) return
            stroke.points.add(x to y)
            outbox.add(x to y)
        }
        changed()
    }

    /** The finger was lifted. */
    fun finish() {
        val stroke = active ?: return
        stroke.done = true
        stroke.finished = app.clock()
        flush()
        changed()
    }

    fun undo() {
        if (!available) return
        // A stroke being drawn is finished first, so the other computer undoes that one.
        finish()
        if (strokes.isNotEmpty()) strokes.removeAt(strokes.size - 1)
        send(buildJsonObject { put("kind", "undo") })
        changed()
    }

    fun clear() {
        if (!available) return
        finish()
        strokes.clear()
        send(buildJsonObject { put("kind", "clear") })
        changed()
    }

    fun cycleWidth() { width = width % 4 + 1 }

    /** Sends the points drawn since the last message. */
    fun flush() {
        val stroke = active ?: return
        val points = outbox.toMutableList()
        outbox.clear()
        if (points.isEmpty() && !stroke.done) return
        do {
            val part = points.take(MAX_MESSAGE)
            repeat(part.size) { points.removeAt(0) }
            send(
                buildJsonObject {
                    put("kind", "stroke"); put("id", stroke.id); put("tool", stroke.tool.wire)
                    put("color", stroke.color); put("width", stroke.width)
                    put("points", JsonArray(part.map { (x, y) -> JsonArray(listOf(JsonPrimitive(x), JsonPrimitive(y))) }))
                    put("done", stroke.done && points.isEmpty())
                },
            )
        } while (points.isNotEmpty())
        firstSent = true
        if (stroke.done) active = null
    }

    /** Opacity of the shown strokes now (they fade after the last change). */
    fun opacity(now: Long): Float {
        if (active != null) return 1f
        val since = now - lastChange
        return when {
            since < FADE_AFTER -> 1f
            since < FADE_AFTER + FADE -> 1f - (since - FADE_AFTER).toFloat() / FADE
            else -> 0f
        }
    }

    /** The strokes to show now (a `drawpic` host shows finished strokes in the picture itself). */
    fun visible(now: Long): List<Stroke> = strokes.filter { !(inPicture && it.done && now - it.finished >= IN_PICTURE_AFTER) }

    private fun changed() {
        lastChange = app.clock()
        revision++
    }

    private fun ensureTicker() {
        if (ticker?.isActive == true) return
        ticker = app.scope.launch {
            while (isActive) {
                delay(BATCH)
                flush()
                val now = app.clock()
                if (active == null && now - lastChange > FADE_AFTER + FADE) {
                    if (strokes.isNotEmpty()) { strokes.clear(); revision++ }
                    break
                }
                revision++ // keeps the fade going
            }
        }
    }

    private fun send(message: JsonObject) {
        val session = session ?: app.remote.session ?: return
        if (queue.size >= 256) queue.clear()
        queue.addLast(session to message)
        if (sending) return
        sending = true
        app.scope.launch {
            try {
                while (queue.isNotEmpty()) {
                    val (target, next) = queue.removeFirst()
                    if (target != this@DrawModel.session) continue
                    // The session reports its own end; a lost drawing message is not an error.
                    runCatching { app.engine.perform("remote_draw", JsonObject(mapOf("session" to JsonPrimitive(target), "message" to next)).toString()) }
                }
            } finally {
                sending = false
            }
        }
    }

    companion object {
        const val FEATURE = "draw"
        const val IN_PICTURE = "drawpic"
        const val MAX_MESSAGE = 64
        const val MAX_STROKE = 4096
        const val MAX_STROKES = 100
        const val FADE_AFTER = 5000L
        const val FADE = 600L
        const val IN_PICTURE_AFTER = 1500L
        const val BATCH = 50L
        /** Red, yellow, green, blue, white (as the host draws them). */
        val PALETTE = longArrayOf(0xFFE5322D, 0xFFFFD400, 0xFF22B14C, 0xFF1E7BFF, 0xFFFFFFFF)
        val COLOR_NAMES = listOf("Red", "Yellow", "Green", "Blue", "White")
        private val PEN_WIDTHS = floatArrayOf(3f, 5f, 8f, 12f)
        private val HIGHLIGHT_WIDTHS = floatArrayOf(14f, 20f, 28f, 36f)

        /** Line width on a picture `height` pixels high. */
        fun lineWidth(tool: Tool, width: Int, height: Float): Float {
            val base = (if (tool == Tool.Highlighter) HIGHLIGHT_WIDTHS else PEN_WIDTHS)[width.coerceIn(1, 4) - 1]
            return maxOf(1f, base * maxOf(1f, height) / 1080f)
        }
    }
}
