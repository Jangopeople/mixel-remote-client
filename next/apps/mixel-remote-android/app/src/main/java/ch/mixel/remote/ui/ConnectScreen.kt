package ch.mixel.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material.icons.filled.VerifiedUser
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.foundation.selection.toggleable
import androidx.compose.ui.semantics.Role
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ExposedDropdownMenuAnchorType
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.app.EnrollmentModel
import ch.mixel.remote.app.Section
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L

/** Connect: a Remote ID (computer, or phone with Mixel Quick Support), invitations from colleagues. */
@Composable
fun ConnectScreen(app: AppModel) {
    var partner by rememberSaveable { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    val demo = LocalDemo.current
    var showHelp by remember { mutableStateOf(demo == "help") }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        if (!app.signedIn) {
            MixelCard {
                Text(L("Sign in to access managed devices."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                PrimaryButton(L("Sign in")) { app.section = Section.Account }
            }
        } else if (app.enrollment.needsSetup) {
            EnrollmentCard(app)
        }
        MixelCard {
            CardTitle(L("Partner connection ID"), Icons.Filled.SwapHoriz)
            RemoteIdField(partner, { partner = it })
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text(L("One-time password, if provided")) },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth().toggleable(app.unattended, role = Role.Checkbox) { app.unattended = it },
            ) {
                Checkbox(app.unattended, onCheckedChange = null)
                Spacer(Modifier.width(8.dp))
                Text(L("Connect without confirmation (unattended access)"))
            }
            PrimaryButton(L("Connect"), enabled = !app.busy && app.signedIn, modifier = Modifier.fillMaxWidth()) {
                val id = Format.parseRemoteId(partner)
                if (id == null) {
                    app.error = L("Enter a nine-digit connection ID")
                } else {
                    val supplied = password
                    password = ""
                    app.connect(id, supplied, unattended = app.unattended)
                }
            }
            Text(L("Enter the 9-digit ID shown by the Mixel Remote host."), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if ((app.busy || app.remote.phase == "outgoing") && app.progress.isNotEmpty()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(10.dp))
                    Text(app.progress, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            MessageLine(app.error)
        }
        if (app.account.account?.staff == true) {
            MixelCard {
                CardTitle(L("Help someone on their phone"), Icons.Filled.PhoneAndroid)
                Text(L("The person opens Mixel Quick Support on their phone and tells you the ID it shows."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedButton(onClick = { showHelp = true }) { Text(L("How do I help someone on their phone?")) }
            }
        }
        if (app.remote.invitations.isNotEmpty()) {
            MixelCard {
                CardTitle(L("A colleague invites you to a session"), Icons.Filled.Groups)
                app.remote.invitations.forEach { invitation ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(L("Computer %@", Format.remoteId(invitation.host)), modifier = Modifier.weight(1f))
                        TextButton(onClick = { app.session.dismiss(invitation) }) { Text(L("Dismiss")) }
                        PrimaryButton(L("Join")) { app.session.join(invitation) }
                    }
                }
            }
        }
        MixelCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                val (kind, text) = when {
                    app.remote.connected -> BadgeKind.Online to L("Online")
                    app.remote.phase == "error" -> BadgeKind.Attention to L("Connection problem")
                    else -> BadgeKind.Offline to L("Offline")
                }
                Column(Modifier.weight(1f)) {
                    StatusBadge(kind, text)
                    Text(app.connectionMessage, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (app.signedIn) {
                    if (app.remote.disconnected) TextButton(onClick = { app.goOnline() }) { Text(L("Connect to Mixel Remote")) }
                    else TextButton(onClick = { app.goOffline() }) { Text(L("Go offline")) }
                }
            }
            if (app.remote.host != null) {
                Text(L("Connection ID") + " " + Format.remoteId(app.remote.host), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
    if (showHelp) PhoneHelpDialog { showHelp = false }
}

/** How phone help works (Mixel Quick Support by ID). */
@Composable
fun PhoneHelpDialog(onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(L("Help someone on their phone")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                listOf(
                    L("The person installs Mixel Quick Support from the App Store or Google Play."),
                    L("They open it and read you the 9-digit ID it shows."),
                    L("You type the ID under Connect and choose Connect."),
                    L("On an iPhone the person taps “Start sharing my screen”; on Android they allow the session."),
                ).forEachIndexed { index, step ->
                    Row {
                        Text("${index + 1}.", fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.primary, modifier = Modifier.width(24.dp))
                        Text(step)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(L("Done")) } },
    )
}

/** Set up this phone once: the path depends on the account (company, Mixel team, personal). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EnrollmentCard(app: AppModel) {
    val enrollment = app.enrollment
    var group by remember { mutableStateOf<String?>(null) }
    var company by remember { mutableStateOf("") }
    MixelCard {
        CardTitle(L("Set up this phone"), Icons.Filled.VerifiedUser)
        when (enrollment.path) {
            EnrollmentModel.Path.Company -> {
                Text(L("Add this phone to %@ so you can connect from it.", app.company.tenant?.name ?: ""), color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(enrollment.name, { enrollment.name = it }, label = { Text(L("Name of this phone")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                if (app.devices.groups.isNotEmpty()) {
                    var open by remember { mutableStateOf(false) }
                    ExposedDropdownMenuBox(open, { open = it }) {
                        OutlinedTextField(
                            value = app.devices.groups.firstOrNull { it.groupId == group }?.name ?: L("No group"),
                            onValueChange = {}, readOnly = true, label = { Text(L("Group")) },
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(open) },
                            modifier = Modifier.menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable).fillMaxWidth(),
                        )
                        ExposedDropdownMenu(open, { open = false }) {
                            DropdownMenuItem({ Text(L("No group")) }, { group = null; open = false })
                            app.devices.groups.forEach { item -> DropdownMenuItem({ Text(item.name) }, { group = item.groupId; open = false }) }
                        }
                    }
                }
                PrimaryButton(L("Add this phone"), enabled = !enrollment.working && app.devices.addBlocked.isEmpty()) { enrollment.addToCompany(group) }
                MessageLine(app.devices.addBlocked, error = false)
            }
            EnrollmentModel.Path.CompanyMember ->
                Text(L("Devices you add appear here. Ask an administrator of your company for access to other devices."), color = MaterialTheme.colorScheme.onSurfaceVariant)
            EnrollmentModel.Path.Personal -> {
                Text(L("Register this phone with your personal account."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(enrollment.name, { enrollment.name = it }, label = { Text(L("Device name")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                PrimaryButton(L("Register this phone"), enabled = !enrollment.working) { enrollment.registerPersonal() }
            }
            EnrollmentModel.Path.Team -> {
                Text(L("Enroll this phone against its existing ISM record, or add it to a company."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(enrollment.recordSearch, { enrollment.recordSearch = it }, label = { Text(L("Device name")) }, singleLine = true, modifier = Modifier.weight(1f))
                    TextButton(onClick = { enrollment.loadTeamRecords() }) { Text(L("Find device")) }
                }
                enrollment.records.forEach { record ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(record.name, modifier = Modifier.weight(1f))
                        TextButton(onClick = { enrollment.enroll(record) }, enabled = !enrollment.working) { Text(L("Enroll this phone")) }
                    }
                }
                if (enrollment.companies.isNotEmpty()) {
                    Text(L("This phone is not in the list? Add it"), fontWeight = FontWeight.Medium)
                    var open by remember { mutableStateOf(false) }
                    ExposedDropdownMenuBox(open, { open = it }) {
                        OutlinedTextField(
                            value = enrollment.companies.firstOrNull { it.clientId == company }?.clientName ?: "",
                            onValueChange = {}, readOnly = true, label = { Text(L("Company")) },
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(open) },
                            modifier = Modifier.menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable).fillMaxWidth(),
                        )
                        ExposedDropdownMenu(open, { open = false }) {
                            enrollment.companies.forEach { item -> DropdownMenuItem({ Text(item.clientName) }, { company = item.clientId; open = false }) }
                        }
                    }
                    OutlinedTextField(enrollment.name, { enrollment.name = it }, label = { Text(L("Name of this phone")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedButton(onClick = { enrollment.addAndEnroll(company) }, enabled = company.isNotEmpty() && !enrollment.working) { Text(L("Add and enroll this phone")) }
                }
            }
            EnrollmentModel.Path.None -> {
                Text(L("Create a company to manage your devices and team, or a personal space for your own devices."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                PrimaryButton(L("Create a company")) { app.section = Section.Account }
            }
        }
        if (enrollment.working) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
        MessageLine(enrollment.error)
    }
}
