package ch.mixel.remote.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Assessment
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.app.Section
import ch.mixel.remote.support.L
import kotlinx.coroutines.launch

/** What the app can do outside the engine (files, links), provided by the activity. */
interface Platform {
    /** Asks for a document to send; calls back with a readable copy's path, or null. */
    fun chooseFileToSend(done: (String?) -> Unit) {}
    /** Saves a received file (path in the app's folder) where the person chooses. */
    fun saveReceived(path: String, name: String) {}
    /** Saves a session report (CSV or PDF) where the person chooses. */
    fun saveReport(name: String, mime: String, bytes: ByteArray) {}
    /** A private path for a file being received. */
    fun receivePath(name: String): String = name
    /** The store.mixel.ch download the app checks for updates (the main Android download). */
    val updateVariant: String get() = ""
    val storeUrl: String get() = "https://store.mixel.ch/app/mixel-remote"

    object None : Platform
}

/** Demo screens only (screenshots): which dialog starts open ("chat", "files", "help"). */
val LocalDemo = androidx.compose.runtime.staticCompositionLocalOf<String?> { null }

/** Devices, Connect and Account as tabs; Settings from Account; a session covers the whole screen. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RootScreen(app: AppModel, platform: Platform = Platform.None, startInSettings: Boolean = false, startInReports: Boolean = false) {
    var settingsOpen by rememberSaveable { mutableStateOf(startInSettings) }
    var reportsOpen by rememberSaveable { mutableStateOf(startInReports) }
    if (app.session.shown) {
        SessionScreen(app, platform)
        return
    }
    // After a session with a company device: an optional note for the session report.
    app.reports.notePrompt?.let { prompt ->
        val scope = androidx.compose.runtime.rememberCoroutineScope()
        NoteDialog(
            title = L("Session note"), subtitle = L("Session with %@. Add a note? It appears in the session report of your company.", prompt.name),
            initial = "", hint = L("Up to 4000 characters. Leave it empty to remove the note."), label = L("Note (optional)"), skip = L("Skip"),
            onDismiss = { app.reports.dismissNote() },
        ) { text -> scope.launch { app.reports.saveSessionNote(text) } }
    }
    val subpage = settingsOpen || reportsOpen
    BackHandler(enabled = subpage) { settingsOpen = false; reportsOpen = false }
    val title = when {
        reportsOpen -> L("Session reports")
        settingsOpen -> L("Settings")
        app.section == Section.Devices -> L("Devices")
        app.section == Section.Connect -> L("Connect")
        else -> L("Account")
    }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text(title) },
                navigationIcon = {
                    if (subpage) {
                        IconButton(onClick = { settingsOpen = false; reportsOpen = false }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = L("Back")) }
                    }
                },
                actions = {
                    if (!subpage && app.section == Section.Account) {
                        if (app.company.tenant != null) {
                            IconButton(onClick = { reportsOpen = true }) { Icon(Icons.Filled.Assessment, contentDescription = L("Session reports")) }
                        }
                        IconButton(onClick = { settingsOpen = true }) { Icon(Icons.Filled.Settings, contentDescription = L("Settings")) }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
        bottomBar = {
            if (!subpage) {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceContainer) {
                    val colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = MaterialTheme.colorScheme.primary,
                        selectedTextColor = MaterialTheme.colorScheme.primary,
                        indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                    )
                    NavigationBarItem(app.section == Section.Devices, { app.section = Section.Devices }, { Icon(Icons.Filled.Devices, null) }, label = { Text(L("Devices")) }, colors = colors)
                    NavigationBarItem(app.section == Section.Connect, { app.section = Section.Connect }, { Icon(Icons.Filled.SwapHoriz, null) }, label = { Text(L("Connect")) }, colors = colors)
                    NavigationBarItem(app.section == Section.Account, { app.section = Section.Account }, { Icon(Icons.Filled.Person, null) }, label = { Text(L("Account")) }, colors = colors)
                }
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when {
                reportsOpen -> ReportsScreen(app, platform)
                settingsOpen -> SettingsScreen(app, platform)
                app.section == Section.Devices -> DevicesScreen(app)
                app.section == Section.Connect -> ConnectScreen(app)
                else -> AccountScreen(app)
            }
        }
    }
}
