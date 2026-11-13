package ch.mixel.remote.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
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

/** What the app can do outside the engine (files, links), provided by the activity. */
interface Platform {
    /** Asks for a document to send; calls back with a readable copy's path, or null. */
    fun chooseFileToSend(done: (String?) -> Unit) {}
    /** Saves a received file (path in the app's folder) where the person chooses. */
    fun saveReceived(path: String, name: String) {}
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
fun RootScreen(app: AppModel, platform: Platform = Platform.None, startInSettings: Boolean = false) {
    var settingsOpen by rememberSaveable { mutableStateOf(startInSettings) }
    if (app.session.shown) {
        SessionScreen(app, platform)
        return
    }
    BackHandler(enabled = settingsOpen) { settingsOpen = false }
    val title = when {
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
                    if (settingsOpen) {
                        IconButton(onClick = { settingsOpen = false }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = L("Back")) }
                    }
                },
                actions = {
                    if (!settingsOpen && app.section == Section.Account) {
                        IconButton(onClick = { settingsOpen = true }) { Icon(Icons.Filled.Settings, contentDescription = L("Settings")) }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
        bottomBar = {
            if (!settingsOpen) {
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
                settingsOpen -> SettingsScreen(app, platform)
                app.section == Section.Devices -> DevicesScreen(app)
                app.section == Section.Connect -> ConnectScreen(app)
                else -> AccountScreen(app)
            }
        }
    }
}
