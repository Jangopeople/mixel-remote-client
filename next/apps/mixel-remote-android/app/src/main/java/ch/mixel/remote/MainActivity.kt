package ch.mixel.remote

import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.browser.customtabs.CustomTabColorSchemeParams
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.edit
import androidx.core.net.toUri
import androidx.lifecycle.lifecycleScope
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.app.AppPreferences
import ch.mixel.remote.demo.DemoFixtures
import ch.mixel.remote.engine.CoreEngine
import ch.mixel.remote.engine.message
import ch.mixel.remote.support.Translator
import ch.mixel.remote.ui.LocalDemo
import ch.mixel.remote.ui.MixelMark
import ch.mixel.remote.ui.MixelTheme
import ch.mixel.remote.ui.Platform
import ch.mixel.remote.ui.RootScreen
import java.io.File
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.launch

/** The app process: one engine and one app model for as long as the process lives. */
class MixelRemoteApp : Application() {
    val scope = MainScope()
    var model: AppModel? = null
        private set
    var failure = ""
        private set

    override fun onCreate() {
        super.onCreate()
        Translator.load(assets.open("texts.json"), assets.open("engine-texts.json"))
        val prefs = getSharedPreferences("mixel-remote", Context.MODE_PRIVATE)
        Translator.store = object : Translator.ChoiceStore {
            override fun get() = prefs.getString("language", null)
            override fun set(value: String?) { prefs.edit { if (value == null) remove("language") else putString("language", value) } }
        }
    }

    fun model(): AppModel? {
        model?.let { return it }
        return try {
            val prefs = getSharedPreferences("mixel-remote", Context.MODE_PRIVATE)
            val preferences = object : AppPreferences {
                override fun get(key: String) = prefs.getString(key, null)
                override fun set(key: String, value: String?) { prefs.edit { if (value == null) remove(key) else putString(key, value) } }
            }
            // LAB (debug builds only, with a core built with FEATURES=qa-session): a throwaway QA
            // account's session file instead of the browser sign-in. Release builds never read it.
            val qa = File(noBackupFilesDir, "qa-session.json")
            if (BuildConfig.DEBUG && qa.exists()) android.system.Os.setenv("MIXEL_QA_SESSION_FILE", qa.absolutePath, true)
            AppModel(CoreEngine.create(this), scope, preferences, appCallback = CALLBACK).also {
                it.enrollment.deviceModel = Build.MODEL ?: "Android"
                model = it
            }
        } catch (error: Throwable) {
            failure = message(error)
            null
        }
    }

    companion object {
        /** Where the browser sign-in returns (only this address is accepted by the engine and the sign-in service). */
        const val CALLBACK = "ch.mixel.remote://auth/callback"
    }
}

class MainActivity : ComponentActivity(), Platform {
    private var model by mutableStateOf<AppModel?>(null)
    private var demo: String? = null
    private var pendingSend: ((String?) -> Unit)? = null
    private var pendingSave: File? = null

    private val openDocument = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> copyToSend(uri) }
    private val createDocument = registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri -> saveTo(uri) }
    private var pendingReport: ByteArray? = null
    private val createCsv = registerForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { uri -> writeReport(uri) }
    private val createPdf = registerForActivityResult(ActivityResultContracts.CreateDocument("application/pdf")) { uri -> writeReport(uri) }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val application = application as MixelRemoteApp
        demo = if (BuildConfig.DEBUG) intent.getStringExtra("demo") else null
        intent.getStringExtra("language")?.takeIf { BuildConfig.DEBUG }?.let { Translator.choice = it }
        val screen = demo
        if (screen != null) {
            lifecycleScope.launch { model = DemoFixtures.screen(screen, lifecycleScope) }
        } else {
            model = application.model()?.also { app ->
                app.openUrl = { url -> open(url, custom = false) }
                app.openSignIn = { url -> open(url, custom = true) }
                app.start()
            }
        }
        setContent {
            MixelTheme {
                val app = model
                if (app != null) {
                    LaunchedEffect(app.session.shown) {
                        if (app.session.shown) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    }
                    CompositionLocalProvider(LocalDemo provides demo) {
                        RootScreen(app, this, startInSettings = demo == "settings", startInReports = demo == "reports")
                    }
                } else if (screen == null) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                        Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically), horizontalAlignment = Alignment.CenterHorizontally) {
                            MixelMark(56.dp)
                            Text("Mixel Remote could not start", style = MaterialTheme.typography.titleLarge)
                            Text(application.failure, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
        handle(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    /** The browser sign-in came back (ch.mixel.remote://auth/callback?state=…&code=…): the engine checks and finishes it. */
    private fun handle(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme == "ch.mixel.remote" && data.host == "auth") {
            model?.completeSignIn(data.toString())
            // Never handle the same answer twice (rotation, back to the app).
            intent.data = null
        }
    }

    override fun onResume() {
        super.onResume()
        // Back in front: the session or sign-in may have moved on.
        if (demo == null) model?.let { app -> lifecycleScope.launch { app.refreshAccount(); app.refreshRemote() } }
    }

    private fun open(url: String, custom: Boolean) {
        val uri = url.toUri()
        if (uri.scheme != "https") return
        try {
            if (custom) {
                val colors = CustomTabColorSchemeParams.Builder().setToolbarColor(0xFFBA2034.toInt()).build()
                CustomTabsIntent.Builder().setDefaultColorSchemeParams(colors).setShowTitle(true).build().launchUrl(this, uri)
            } else {
                startActivity(Intent(Intent.ACTION_VIEW, uri))
            }
        } catch (_: Exception) {
            model?.error = ch.mixel.remote.support.L("No browser is available to open the page")
        }
    }

    // Files

    override fun chooseFileToSend(done: (String?) -> Unit) {
        pendingSend = done
        openDocument.launch(arrayOf("*/*"))
    }

    private fun copyToSend(uri: Uri?) {
        val done = pendingSend ?: return
        pendingSend = null
        if (uri == null) { done(null); return }
        lifecycleScope.launch {
            val path = runCatching {
                val name = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) cursor.getString(0) else null
                }?.replace(Regex("[/\\\\:]"), "_")?.take(120) ?: "file"
                val folder = File(cacheDir, "outgoing/${System.currentTimeMillis()}").apply { mkdirs() }
                val target = File(folder, name)
                contentResolver.openInputStream(uri)!!.use { input -> target.outputStream().use { input.copyTo(it) } }
                target.absolutePath
            }.getOrNull()
            done(path)
        }
    }

    override fun receivePath(name: String): String {
        val safe = name.replace(Regex("[/\\\\:]"), "_").take(120).ifEmpty { "file" }
        val folder = File(filesDir, "received/${System.currentTimeMillis()}").apply { mkdirs() }
        return File(folder, safe).absolutePath
    }

    override fun saveReport(name: String, mime: String, bytes: ByteArray) {
        pendingReport = bytes
        if (mime == "application/pdf") createPdf.launch(name) else createCsv.launch(name)
    }

    private fun writeReport(uri: Uri?) {
        val bytes = pendingReport ?: return
        pendingReport = null
        if (uri == null) { model?.reports?.notice = ch.mixel.remote.support.L("Export cancelled."); return }
        lifecycleScope.launch {
            runCatching { contentResolver.openOutputStream(uri)!!.use { it.write(bytes) } }
                .onSuccess {
                    val name = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                        if (cursor.moveToFirst()) cursor.getString(0) else null
                    } ?: ""
                    model?.reports?.notice = ch.mixel.remote.support.L("Saved as %@", name)
                }
                .onFailure { model?.reports?.notice = ch.mixel.remote.support.L("The file could not be saved") }
        }
    }

    override fun saveReceived(path: String, name: String) {
        pendingSave = File(path)
        createDocument.launch(name)
    }

    private fun saveTo(uri: Uri?) {
        val source = pendingSave ?: return
        pendingSave = null
        if (uri == null) return
        lifecycleScope.launch {
            runCatching { contentResolver.openOutputStream(uri)!!.use { output -> source.inputStream().use { it.copyTo(output) } } }
                .onFailure { model?.session?.error = ch.mixel.remote.support.L("The file could not be saved") }
        }
    }
}
