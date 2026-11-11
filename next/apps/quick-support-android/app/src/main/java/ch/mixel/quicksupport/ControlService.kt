package ch.mixel.quicksupport

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ComponentName
import android.content.Context
import android.graphics.Path
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.DisplayMetrics
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject

/**
 * Carries out the technician's taps, swipes and typing. The user switches it on in the system's
 * Accessibility settings; it acts only on input that arrives through a support session the user
 * allowed, and it reads nothing from the screen except the text field that has the focus.
 */
class ControlService : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private var x = 0f
    private var y = 0f
    private var down = false
    private var downAt = 0L
    private val points = ArrayList<Pair<Float, Float>>()

    override fun onServiceConnected() { instance = this }
    override fun onUnbind(intent: android.content.Intent?): Boolean { instance = null; return super.onUnbind(intent) }
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    private fun size(): Pair<Int, Int> {
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        (getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay.getRealMetrics(metrics)
        return metrics.widthPixels to metrics.heightPixels
    }

    private fun handle(event: JSONObject) {
        when (event.optString("type")) {
            "pointer" -> {
                val (w, h) = size()
                x = event.optInt("x") * (w - 1) / 65535f
                y = event.optInt("y") * (h - 1) / 65535f
                if (down && points.size < 400) points.add(x to y)
            }
            "button" -> when (event.optInt("button")) {
                1 -> if (event.optBoolean("down")) {
                    down = true; downAt = SystemClock.uptimeMillis(); points.clear(); points.add(x to y)
                } else if (down) {
                    down = false
                    stroke(points.toList(), SystemClock.uptimeMillis() - downAt)
                }
                // The other buttons stand for the phone's own keys.
                2 -> if (event.optBoolean("down")) performGlobalAction(GLOBAL_ACTION_BACK)
                3 -> if (event.optBoolean("down")) performGlobalAction(GLOBAL_ACTION_HOME)
            }
            "wheel" -> {
                // One notch of the wheel moves the content like a short swipe of a finger.
                val dy = event.optInt("vertical") / 120f * 240f
                val dx = event.optInt("horizontal") / 120f * 240f
                if (dx != 0f || dy != 0f) stroke(listOf(x to y, (x + dx) to (y + dy)), 180)
            }
            "key" -> if (event.optBoolean("down")) key(event.optInt("usage"))
            "text" -> type(event.optString("text"))
            "release" -> down = false
        }
    }

    /** One finger movement: a tap, a long press (held in place) or a swipe along the points. */
    private fun stroke(path: List<Pair<Float, Float>>, heldMillis: Long) {
        if (path.isEmpty()) return
        val (w, h) = size()
        val line = Path()
        val clamp = { p: Pair<Float, Float> -> p.first.coerceIn(0f, w - 1f) to p.second.coerceIn(0f, h - 1f) }
        val first = clamp(path.first())
        line.moveTo(first.first, first.second)
        for (point in path.drop(1)) clamp(point).let { line.lineTo(it.first, it.second) }
        val duration = heldMillis.coerceIn(50, 4000)
        try {
            dispatchGesture(GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(line, 0, duration)).build(), null, null)
        } catch (_: Exception) {
        }
    }

    private fun focused(): AccessibilityNodeInfo? = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.takeIf { it.isEditable }

    private fun type(text: String) {
        val node = focused() ?: return
        // Password fields report no text; typing then starts from what the technician sends.
        val current = if (node.isPassword || node.isShowingHintText) "" else node.text?.toString().orEmpty()
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, current + text) })
    }

    private fun key(usage: Int) {
        when (usage) {
            0x29 -> performGlobalAction(GLOBAL_ACTION_BACK) // Esc
            0xE3, 0xE7 -> performGlobalAction(GLOBAL_ACTION_HOME) // the system key of a keyboard
            0x2A -> focused()?.let { node -> // Backspace
                val current = if (node.isShowingHintText) "" else node.text?.toString().orEmpty()
                if (current.isNotEmpty()) node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, current.dropLast(1)) })
            }
            0x28 -> focused()?.let { node -> // Enter: the keyboard's action key (search, send, next)
                if (android.os.Build.VERSION.SDK_INT >= 30) node.performAction(android.R.id.accessibilityActionImeEnter)
            }
        }
    }

    companion object {
        @Volatile private var instance: ControlService? = null

        /** Whether the user has switched the service on in the system settings. */
        fun enabled(context: Context): Boolean {
            val wanted = ComponentName(context, ControlService::class.java).flattenToString()
            val list = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
            return list.split(':').any { it.equals(wanted, ignoreCase = true) }
        }

        /** Input from the technician (any thread); ignored when the service is off. */
        fun input(event: JSONObject) {
            val service = instance ?: return
            service.main.post { service.handle(event) }
        }

        fun release() { instance?.let { service -> service.main.post { service.down = false } } }
    }
}
