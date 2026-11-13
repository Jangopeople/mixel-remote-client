package ch.mixel.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.model.SelfRemoval
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L
import kotlinx.coroutines.launch

/** Settings: stay signed in, language, this phone, updates, About and legal. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SettingsScreen(app: AppModel, platform: Platform) {
    val settings = app.settings
    val enrollment = app.enrollment
    val scope = rememberCoroutineScope()
    var confirmRemove by remember { mutableStateOf(false) }
    var removalResult by remember { mutableStateOf("") }
    LaunchedEffect(Unit) { settings.load() }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        MixelCard {
            CardTitle(L("General"), Icons.Filled.Tune)
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth().toggleable(
                    value = settings.startup?.remembered ?: false, enabled = app.signedIn, role = Role.Switch,
                    onValueChange = { settings.setStaySignedIn(it) },
                ),
            ) {
                Text(L("Stay signed in on this phone"), modifier = Modifier.weight(1f))
                Switch(checked = settings.startup?.remembered ?: false, onCheckedChange = null, enabled = app.signedIn)
            }
        }
        MixelCard {
            CardTitle(L("Language"), Icons.Filled.Language)
            listOf("auto" to L("Automatic (this phone’s language)"), "en" to "English", "de" to "Deutsch", "fr" to "Français", "it" to "Italiano").forEach { (value, label) ->
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().selectable(settings.language == value, role = Role.RadioButton) { settings.chooseLanguage(value) },
                ) {
                    RadioButton(selected = settings.language == value, onClick = null)
                    Spacer(Modifier.width(10.dp))
                    Text(label)
                }
            }
        }
        MixelCard {
            CardTitle(L("This phone"), Icons.Filled.PhoneAndroid)
            Text(L("Connection ID") + ": " + Format.remoteId(app.remote.host ?: enrollment.current?.connectionId))
            when {
                settings.removalPending -> MessageLine(L("This phone is being removed from Mixel Remote. It is removed as soon as it is online."), error = false)
                enrollment.current != null || app.remote.host != null -> DangerButton(L("Remove this phone from Mixel Remote"), enabled = !enrollment.working) { confirmRemove = true }
                else -> Text(L("This phone is not set up yet."), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            MessageLine(removalResult, error = false)
            MessageLine(enrollment.error)
        }
        MixelCard {
            CardTitle(L("Updates"), Icons.Filled.SystemUpdate)
            Text(L("Version") + ": ${app.info.version}")
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { settings.checkForUpdate(platform.updateVariant) }) { Text(L("Check for updates")) }
                settings.update?.let {
                    Text(L("Version %@ is available.", it.version), color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                    TextButton(onClick = { app.openUrl(platform.storeUrl) }) { Text(L("Download")) }
                } ?: run {
                    if (settings.updateChecked) Text(L("This is the newest version."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        MixelCard {
            CardTitle(L("About and legal"), Icons.Filled.Info)
            Row(verticalAlignment = Alignment.CenterVertically) {
                MixelMark(44.dp)
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("Mixel Remote", style = MaterialTheme.typography.titleMedium)
                    Text(L("Version %@", app.info.version), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Text("© Mixel IT and Corporate Services", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow {
                TextButton(onClick = { settings.openLegal("terms") }) { Text(L("Terms of Service")) }
                TextButton(onClick = { settings.openLegal("privacy") }) { Text(L("Privacy Policy")) }
                TextButton(onClick = { settings.openLegal("dpa") }) { Text(L("Data Processing Agreement")) }
                TextButton(onClick = { settings.openLegal("subprocessors") }) { Text(L("Sub-processors")) }
            }
        }
        MessageLine(settings.error)
    }
    if (confirmRemove) {
        ConfirmDialog(
            L("Remove this phone from Mixel Remote?"),
            L("It can no longer connect until it is added again."),
            L("Remove this phone"),
            onDismiss = { confirmRemove = false },
        ) {
            scope.launch {
                removalResult = when (enrollment.removeThisDevice()) {
                    SelfRemoval.removed -> L("This phone was removed from Mixel Remote.")
                    SelfRemoval.queued -> L("Saved. This phone is removed as soon as Mixel Remote can be reached.")
                    SelfRemoval.stale -> L("This phone's registration changed since; nothing was removed.")
                    null -> ""
                }
            }
        }
    }
}
