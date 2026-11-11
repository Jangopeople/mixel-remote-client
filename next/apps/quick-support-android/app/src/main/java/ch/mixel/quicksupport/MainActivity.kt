package ch.mixel.quicksupport

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** The whole app is one screen: this phone's ID to tell the technician, the status, allow or end the session. */
class MainActivity : Activity(), Support.Screen {
    private lateinit var status: TextView
    private lateinit var number: TextView
    private lateinit var end: Button
    private lateinit var disconnect: Button
    private lateinit var controlState: TextView
    private var asking: AlertDialog? = null

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private fun card(vararg views: View): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(16), dp(16), dp(16))
        background = GradientDrawable().apply { setColor(Color.WHITE); cornerRadius = dp(14).toFloat(); setStroke(dp(1), Color.parseColor("#E3E6EC")) }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(16) }
        views.forEach { addView(it) }
    }
    private fun text(resource: Int, size: Float = 15f, color: String = "#172033", bold: Boolean = false) = TextView(this).apply {
        if (resource != 0) setText(resource)
        textSize = size; setTextColor(Color.parseColor(color)); if (bold) typeface = Typeface.DEFAULT_BOLD
        setLineSpacing(0f, 1.15f)
    }
    private fun button(resource: Int, primary: Boolean) = Button(this).apply {
        setText(resource); isAllCaps = false; textSize = 16f
        setTextColor(if (primary) Color.WHITE else Color.parseColor("#172033"))
        background = GradientDrawable().apply { setColor(if (primary) Color.parseColor("#B3162F") else Color.WHITE); cornerRadius = dp(10).toFloat(); if (!primary) setStroke(dp(1), Color.parseColor("#E3E6EC")) }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(12) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        status = text(0, 16f)
        number = text(0, 44f, bold = true).apply { letterSpacing = 0.06f; setPadding(0, dp(4), 0, dp(4)); setTextIsSelectable(true) }
        end = button(R.string.end_session, true).apply { setOnClickListener { Support.endSession() } }
        controlState = text(0, 14f, "#5D6678")
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(20), dp(16), dp(32))
            addView(text(R.string.app_name, 24f, bold = true))
            addView(card(text(R.string.your_id, 15f, "#5D6678"), number, status, end))
            addView(card(text(R.string.control_title, 16f, bold = true), controlState, button(R.string.control_open, false).apply { setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) } }))
            addView(text(R.string.privacy, 13f, "#5D6678").apply { setPadding(dp(2), dp(18), dp(2), 0) })
        }
        val scroll = ScrollView(this).apply { addView(column); isFillViewport = true; setBackgroundColor(Color.parseColor("#F5F6F9")) }
        // Keep the content clear of the status bar, the navigation bar and the keyboard.
        scroll.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars() or android.view.WindowInsets.Type.displayCutout() or android.view.WindowInsets.Type.ime())
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                @Suppress("DEPRECATION")
                view.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop, insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            }
            insets
        }
        setContentView(scroll)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
        }
    }

    override fun onResume() {
        super.onResume()
        Support.screen = this
        changed()
        Support.watch(this)
    }
    override fun onPause() {
        if (Support.screen === this) Support.screen = null
        super.onPause()
    }

    override fun changed() {
        val phase = Support.phase
        number.text = Support.remoteId.ifEmpty { "— — —" }
        status.setText(when (phase) {
            Support.Phase.READY -> R.string.status_ready
            Support.Phase.ASKING -> R.string.status_asking
            Support.Phase.NEGOTIATING -> R.string.status_negotiating
            Support.Phase.ACTIVE -> R.string.status_active
            Support.Phase.CONNECTING -> R.string.status_connecting
            else -> if (Support.remoteId.isEmpty()) R.string.status_connecting else R.string.status_waiting
        })
        if (phase == Support.Phase.OFFLINE && Support.message.isNotEmpty() && Support.remoteId.isEmpty()) status.text = Support.message
        end.visibility = if (phase == Support.Phase.ACTIVE || phase == Support.Phase.NEGOTIATING) View.VISIBLE else View.GONE
        controlState.setText(if (ControlService.enabled(this)) R.string.control_on else R.string.control_off)
        if (phase != Support.Phase.ASKING) { asking?.dismiss(); asking = null }
    }

    override fun ask(remoteId: String, staff: Boolean) {
        asking?.dismiss()
        val who = getString(if (staff) R.string.ask_staff else R.string.ask_other, remoteId)
        val what = getString(if (ControlService.enabled(this)) R.string.ask_control_on else R.string.ask_control_off)
        asking = AlertDialog.Builder(this)
            .setTitle(R.string.ask_title)
            .setMessage("$who\n\n$what")
            .setCancelable(false)
            .setNegativeButton(R.string.decline) { _, _ -> Support.decide(false) }
            // Allowing continues with Android's own question about sharing the screen.
            .setPositiveButton(R.string.allow) { _, _ ->
                val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
                // Support needs the whole screen, not one app: ask for exactly that where Android lets us choose.
                val question = if (Build.VERSION.SDK_INT >= 34) manager.createScreenCaptureIntent(android.media.projection.MediaProjectionConfig.createConfigForDefaultDisplay()) else manager.createScreenCaptureIntent()
                @Suppress("DEPRECATION")
                startActivityForResult(question, 1)
            }
            .show()
    }

    @Deprecated("The framework's own result path; this app uses no support library.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != 1) return
        if (resultCode == RESULT_OK && data != null) {
            val service = Intent(this, ShareService::class.java).putExtra(ShareService.EXTRA_PERMISSION, data)
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(service) else startService(service)
        } else {
            Support.decide(false)
        }
    }

    override fun notice(text: String) {
        Toast.makeText(this, text, Toast.LENGTH_LONG).apply { setGravity(Gravity.TOP or Gravity.CENTER_HORIZONTAL, 0, dp(80)) }.show()
    }
}
