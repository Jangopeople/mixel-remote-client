package ch.mixel.remote.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.DesktopWindows
import androidx.compose.material.icons.filled.LaptopMac
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.text.font.FontFamily
import kotlinx.coroutines.launch
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.app.DevicesModel
import ch.mixel.remote.app.Section
import ch.mixel.remote.model.Device
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L

fun osIcon(os: String): ImageVector = when (os.lowercase()) {
    "macos" -> Icons.Filled.LaptopMac
    "windows" -> Icons.Filled.DesktopWindows
    "linux" -> Icons.Filled.Computer
    "ios", "android", "other" -> Icons.Filled.PhoneAndroid
    else -> Icons.Filled.Computer
}

/** Devices: the same directory and status as on the computers, with the reason a device cannot be reached. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DevicesScreen(app: AppModel) {
    val model = app.devices
    // Fresh list whenever the tab is opened (devices added elsewhere appear at once).
    androidx.compose.runtime.LaunchedEffect(app.signedIn) { if (app.signedIn) model.load() }
    PullToRefreshBox(isRefreshing = model.loading && model.devices.isNotEmpty(), onRefresh = { model.reload() }, modifier = Modifier.fillMaxSize()) {
        LazyColumn(
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            if (!app.signedIn) {
                item {
                    MixelCard {
                        Text(L("Sign in to see the devices available to your account."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                        PrimaryButton(L("Sign in")) { app.section = Section.Account }
                    }
                }
            } else {
                item {
                    OutlinedTextField(
                        value = model.search,
                        onValueChange = { model.search = it },
                        placeholder = { Text(L("Device name")) },
                        leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                        keyboardActions = KeyboardActions(onSearch = { model.reload() }),
                        modifier = Modifier.fillMaxWidth().semantics { contentDescription = L("Search devices") },
                    )
                }
            }
            if (app.signedIn && model.manages) item { ManageBar(model) }
            items(model.devices, key = { it.device + it.connectionId }) { device -> DeviceRow(model, device) }
            if (app.signedIn && model.devices.isEmpty() && !model.loading) {
                item {
                    Text(
                        if (model.tenant != null) L("Devices you add appear here. Ask an administrator of your company for access to other devices.")
                        else L("Devices appear here after a Mixel team member enrolls them."),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(8.dp),
                    )
                }
            }
            if (model.page > 0 || model.hasNext) {
                item {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        TextButton(onClick = { model.previousPage() }, enabled = model.page > 0) { Text(L("Previous")) }
                        Text(model.message, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.align(Alignment.CenterVertically))
                        TextButton(onClick = { model.nextPage() }, enabled = model.hasNext) { Text(L("Next")) }
                    }
                }
            }
            item { MessageLine(model.error) }
        }
        if (model.loading && model.devices.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        }
    }
}

/** Company devices: a new group, and deploy links for other computers (owners and administrators). */
@Composable
private fun ManageBar(model: DevicesModel) {
    var newGroup by remember { mutableStateOf(false) }
    var deploy by remember { mutableStateOf(false) }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (model.deploys) OutlinedButton(onClick = { deploy = true }, enabled = model.addBlocked.isEmpty()) {
            Icon(Icons.Filled.Add, null, modifier = Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text(L("Add other computers"))
        }
        if (model.groupsOn) TextButton(onClick = { newGroup = true }) { Text(L("Create group")) }
    }
    if (model.addBlocked.isNotEmpty()) MessageLine(model.addBlocked, error = false)
    if (newGroup) FormDialog(L("Create group"), fields = listOf(FormField("name", L("Group name"))), confirm = L("Create group"), onDismiss = { newGroup = false }) {
        if (model.createGroup(it["name"].orEmpty())) null else model.error
    }
    if (deploy) FormDialog(
        L("Add other computers"),
        L("Creates a deploy link. Run the command it shows on each computer; it joins your company without a sign-in."),
        listOf(FormField("label", L("Label"), "Deploy link"), FormField("days", L("Valid for days"), "7", androidx.compose.ui.text.input.KeyboardType.Number), FormField("computers", L("Number of computers"), "10", androidx.compose.ui.text.input.KeyboardType.Number)),
        L("Create deploy link"),
        onDismiss = { deploy = false },
    ) {
        val days = it["days"]?.toIntOrNull()?.coerceIn(1, 90) ?: 7
        val computers = it["computers"]?.toIntOrNull()?.coerceIn(1, 1000) ?: 10
        if (model.createDeployLink(it["label"].orEmpty().ifEmpty { "Deploy link" }, days, computers, null)) null else model.error
    }
    model.deployLink?.let { link -> DeployLinkDialog(link) { model.deployLink = null } }
}

@Composable
private fun DeployLinkDialog(link: ch.mixel.remote.model.DeployLinkCreated, onDismiss: () -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    fun copy(text: String) = context.getSystemService(android.content.ClipboardManager::class.java)
        .setPrimaryClip(android.content.ClipData.newPlainText("Mixel Remote", text))
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(L("Deploy link created")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(L("Shown only now. Run the command on each computer as administrator."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                FieldLabel(L("Deploy token (the Mac and Linux commands ask for it)"))
                Text(link.token, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = { copy(link.token) }) { Text(L("Copy")) }
                listOf("Windows" to link.windowsCommand, "macOS" to link.macosCommand, "Linux" to link.linuxCommand).filter { it.second.isNotEmpty() }.forEach { (system, command) ->
                    FieldLabel(system)
                    Text(command, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, maxLines = 4)
                    TextButton(onClick = { copy(command) }) { Text(L("Copy")) }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(L("Done")) } },
    )
}

@Composable
private fun DeviceRow(model: DevicesModel, device: Device) {
    val scope = rememberCoroutineScope()
    var menu by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf(false) }
    var moving by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf(false) }
    val presence = model.presence(device)
    val blocked = model.connectBlocked(device)
    val reason = model.reason(device)
    val label = model.label(presence)
    val canWake = presence == DevicesModel.Presence.Offline && model.macs(device).isNotEmpty()
    val kind = when (presence) {
        DevicesModel.Presence.Online -> BadgeKind.Online
        DevicesModel.Presence.Offline -> BadgeKind.Offline
        DevicesModel.Presence.This -> BadgeKind.Busy
        else -> BadgeKind.Unknown
    }
    val actions = buildList {
        if (canWake) add(CustomAccessibilityAction(L("Wake up")) { model.wake(device); true })
        if (model.manages) {
            add(CustomAccessibilityAction(L("Rename")) { renaming = true; true })
            if (model.groupsOn && model.groups.isNotEmpty()) add(CustomAccessibilityAction(L("Move to group")) { moving = true; true })
            add(CustomAccessibilityAction(L("Remove device")) { removing = true; true })
        }
    }
    MixelCard(
        modifier = Modifier
            .alpha(if (blocked.isEmpty() || canWake) 1f else 0.78f)
            .clickable(enabled = blocked.isEmpty(), role = Role.Button) { model.connect(device) }
            .semantics(mergeDescendants = true) {
                contentDescription = "${device.name}, ${Format.os(device.os)}, ${L("Connection ID")} ${Format.remoteId(device.connectionId)}"
                stateDescription = if (reason.isEmpty()) label else "$label. $reason"
                if (blocked.isNotEmpty() && actions.isEmpty()) disabled()
                onClick(label = if (blocked.isEmpty()) L("Connect to %@", device.name) else blocked) { if (blocked.isEmpty()) model.connect(device); blocked.isEmpty() }
                if (actions.isNotEmpty()) customActions = actions
            },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                osIcon(device.os),
                contentDescription = null,
                tint = if (presence == DevicesModel.Presence.Online) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(30.dp),
            )
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(device.name, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
                StatusBadge(kind, label)
                if (reason.isNotEmpty()) Text(reason, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                device.groupName?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(Format.remoteId(device.connectionId), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row {
                    if (canWake) IconButton(onClick = { model.wake(device) }) { Icon(Icons.Filled.PowerSettingsNew, contentDescription = L("Wake up")) }
                    if (model.manages) Box {
                        IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, contentDescription = L("Manage")) }
                        DropdownMenu(menu, { menu = false }) {
                            DropdownMenuItem({ Text(L("Rename")) }, { menu = false; renaming = true })
                            if (model.groupsOn && model.groups.isNotEmpty()) DropdownMenuItem({ Text(L("Move to group")) }, { menu = false; moving = true })
                            HorizontalDivider()
                            DropdownMenuItem({ Text(L("Remove device"), color = MaterialTheme.colorScheme.error) }, { menu = false; removing = true })
                        }
                    }
                }
            }
        }
    }
    if (renaming) FormDialog(L("Rename"), fields = listOf(FormField("name", L("Device name"), device.name)), confirm = L("Rename"), onDismiss = { renaming = false }) {
        if (model.rename(device, it["name"].orEmpty())) null else model.error
    }
    if (removing) ConfirmDialog(
        L("Remove %@?", device.name), L("It can no longer be reached or connect until it is added again."), L("Remove device"),
        onDismiss = { removing = false },
    ) { scope.launch { model.remove(device) } }
    if (moving) AlertDialog(
        onDismissRequest = { moving = false },
        title = { Text(L("Move to group")) },
        text = {
            Column {
                (listOf<Pair<String?, String>>(null to L("No group")) + model.groups.map { it.groupId to it.name }).forEach { (id, name) ->
                    TextButton(onClick = { moving = false; scope.launch { model.move(device, id) } }, enabled = id != device.groupId) { Text(name) }
                }
            }
        },
        confirmButton = { TextButton(onClick = { moving = false }) { Text(L("Cancel")) } },
    )
}
