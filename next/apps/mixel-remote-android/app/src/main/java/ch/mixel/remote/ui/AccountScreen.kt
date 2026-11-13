package ch.mixel.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Business
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PersonRemove
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.app.CompanyModel
import ch.mixel.remote.engine.args
import ch.mixel.remote.engine.run
import ch.mixel.remote.model.Account
import ch.mixel.remote.model.Invite
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L
import ch.mixel.remote.support.Translator
import kotlinx.coroutines.launch

/** Account: sign in with Mixel ID (and its second step), the company with its members and invitations. */
@Composable
fun AccountScreen(app: AppModel) {
    val account = app.account
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        val profile = account.account
        if (account.signedIn && profile != null) {
            ProfileCard(app, profile)
            val company = app.company
            if (company.tenant != null) CompanyCard(app, company)
            else if (company.loaded && !profile.staff) CreateCompanyCard(app, company)
            DangerCard(app)
        } else if (account.phase == "profile_setup") {
            ProfileSetupCard(app)
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                MixelMark(44.dp)
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("Mixel Remote", style = MaterialTheme.typography.titleLarge)
                    Text(L("A secure home for your devices and connections."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            SignInCard(app)
        }
        MessageLine(app.error)
    }
}

@Composable
private fun SignInCard(app: AppModel) {
    val account = app.account
    MixelCard {
        CardTitle(L("Connect with your Mixel account"), Icons.Filled.Key)
        when (account.phase) {
            "starting", "waiting_browser", "verifying" -> {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(10.dp))
                    Text(Translator.translate(account.message))
                }
                TextButton(onClick = { app.cancelSignIn() }) { Text(L("Cancel sign-in")) }
            }
            "mfa_required" -> {
                Text(L("Additional verification is required for this Mixel account"), color = MaterialTheme.colorScheme.onSurfaceVariant)
                val factor = account.factors.firstOrNull()
                factor?.friendlyName?.let { Text(L("Enter the code from the authenticator entry “%@”.", it)) }
                OutlinedTextField(
                    value = app.verificationCode,
                    onValueChange = { value -> app.verificationCode = value.filter { it.isDigit() || it == ' ' }.take(7) },
                    label = { Text(L("Verification code")) },
                    placeholder = { Text("123456") },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { factor?.let { app.verify(it.id) } }),
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    PrimaryButton(L("Verify"), enabled = factor != null && !app.busy) { factor?.let { app.verify(it.id) } }
                    TextButton(onClick = { app.cancelSignIn() }) { Text(L("Cancel sign-in")) }
                }
            }
            else -> {
                Text(L("Choose how you sign in. The sign-in page opens; Mixel Remote continues by itself when you are signed in."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (account.phase == "error") MessageLine(Translator.translate(account.message))
                ProviderButtons(app.providers, enabled = !app.busy) { app.signIn(it) }
                TextButton(onClick = { app.openUrl("https://auth.mixel.ch/if/flow/mixel-remote-enrollment/") }) { Text(L("Create Mixel ID")) }
            }
        }
    }
}

/** A new Mixel ID: create a company (14-day trial) or a personal space before the first use. */
@Composable
private fun ProfileSetupCard(app: AppModel) {
    val scope = rememberCoroutineScope()
    var company by remember { mutableStateOf("") }
    var terms by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    MixelCard {
        CardTitle(L("Set up Mixel Remote"), Icons.Filled.Business)
        Text(L("Mixel ID verified. Enter your name to set up Remote."), color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(L("Create a company"), style = MaterialTheme.typography.titleSmall)
        Text(L("Your company gets a 14-day Business trial, no card needed. You become its owner and can invite your team."), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedTextField(company, { company = it }, label = { Text(L("Company name")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.toggleable(terms, role = Role.Checkbox) { terms = it }) {
            Checkbox(terms, onCheckedChange = null)
            Spacer(Modifier.width(8.dp))
            Text(L("I accept the Terms of Service and the Data Processing Agreement"))
        }
        TextButton(onClick = { app.settings.openLegal("legal") }) { Text(L("Read the Terms of Service, the Data Processing Agreement and the Privacy Policy")) }
        PrimaryButton(L("Create company"), enabled = !app.company.working && company.isNotBlank()) { scope.launch { app.company.create(company, terms) } }
        MessageLine(app.company.error)
        HorizontalDivider()
        Text(L("Or a personal space for your own devices"), style = MaterialTheme.typography.titleSmall)
        OutlinedTextField(name, { name = it }, label = { Text(L("Name shown to your own devices")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedButton(onClick = { app.setUpPersonal(name) }, enabled = name.isNotBlank() && !app.busy) { Text(L("Create personal space")) }
        TextButton(onClick = { app.cancelSignIn() }) { Text(L("Cancel sign-in")) }
    }
}

@Composable
private fun ProfileCard(app: AppModel, profile: Account) {
    MixelCard {
        Row(verticalAlignment = Alignment.Top) {
            Icon(Icons.Filled.AccountCircle, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(44.dp))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(profile.displayName ?: L("Signed in"), style = MaterialTheme.typography.titleMedium)
                Text(kind(app, profile), color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (profile.remembered) Text(L("Stays signed in on this phone"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TextButton(onClick = { app.signOut() }) { Text(L("Sign out")) }
        }
        if (profile.hasPersonal && profile.hasCompany) {
            Text(L("Devices of"), style = MaterialTheme.typography.labelLarge)
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                SegmentedButton(!profile.personal, { app.switchWorkspace(false) }, SegmentedButtonDefaults.itemShape(0, 2)) { Text(L("Company devices")) }
                SegmentedButton(profile.personal, { app.switchWorkspace(true) }, SegmentedButtonDefaults.itemShape(1, 2)) { Text(L("Personal devices")) }
            }
        }
        if (profile.companies.isNotEmpty()) {
            HorizontalDivider()
            Text(L("Company administration"), style = MaterialTheme.typography.titleSmall)
            profile.companies.forEach { item ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(item.companyName, modifier = Modifier.weight(1f))
                    TextButton(onClick = {
                        app.act { app.openUrl(app.engine.run<String>("company_settings_url", args("company_id" to item.companyId))) }
                    }) { Text(L("Manage devices and company")) }
                }
            }
        }
    }
}

private fun kind(app: AppModel, profile: Account): String {
    if (profile.staff) return L("Mixel team account")
    if (profile.personal) return L("Personal account")
    app.company.tenant?.let { return "${it.name} · ${Format.role(it.role)}" }
    return L("Mixel account")
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CompanyCard(app: AppModel, company: CompanyModel) {
    val scope = rememberCoroutineScope()
    var inviting by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf(false) }
    MixelCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) { CardTitle(company.tenant?.name ?: "", Icons.Filled.Business) }
            if (company.administers) TextButton(onClick = { renaming = true }, enabled = !company.readOnly) { Text(L("Rename company")) }
        }
        Text(company.planLine, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (company.usageLine.isNotEmpty()) Text(company.usageLine, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
        if (company.readOnly) MessageLine(L("This company is read-only. You can still remove devices; adding devices and other changes need an active plan."), error = false)
        HorizontalDivider()
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(L("Members"), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            if (company.administers) OutlinedButton(onClick = { inviting = true }, enabled = !company.readOnly) { Text(L("Invite a person")) }
        }
        val me = app.account.account?.profile
        company.members.forEach { member ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(member.name.ifEmpty { member.email })
                    Text(member.email, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (company.administers && member.userId != me) {
                    var open by remember { mutableStateOf(false) }
                    Box {
                        TextButton(onClick = { open = true }, modifier = Modifier.semantics { contentDescription = L("Role of %@: %@", member.name.ifEmpty { member.email }, Format.role(member.role)) }) {
                            Text(Format.role(member.role))
                            Icon(Icons.Filled.MoreVert, contentDescription = null, modifier = Modifier.size(18.dp))
                        }
                        DropdownMenu(open, { open = false }) {
                            listOf("owner", "admin", "technician", "member").forEach { role ->
                                DropdownMenuItem({ Text(Format.role(role)) }, { open = false; scope.launch { company.setRole(member, role) } }, enabled = role != member.role)
                            }
                            HorizontalDivider()
                            DropdownMenuItem(
                                { Text(L("Remove from company"), color = MaterialTheme.colorScheme.error) },
                                { open = false; scope.launch { company.remove(member) } },
                                leadingIcon = { Icon(Icons.Filled.PersonRemove, null, tint = MaterialTheme.colorScheme.error) },
                            )
                        }
                    }
                } else {
                    Text(Format.role(member.role), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (member.userId == me && !company.isOwner) TextButton(onClick = { scope.launch { company.remove(member) } }) { Text(L("Leave")) }
                }
            }
        }
        if (company.administers && company.invites.isNotEmpty()) {
            HorizontalDivider()
            Text(L("Open invitations"), style = MaterialTheme.typography.titleSmall)
            company.invites.forEach { invite ->
                Column {
                    Text(invite.email)
                    Text("${Format.role(invite.role)} · ${mailState(invite)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Row {
                        TextButton(onClick = { scope.launch { company.resend(invite) } }) { Text(L("Send again")) }
                        DangerButton(L("Revoke")) { scope.launch { company.revoke(invite) } }
                    }
                }
            }
        }
        MessageLine(company.notice, error = false)
        MessageLine(company.error)
        if (company.isOwner) {
            HorizontalDivider()
            Text(L("Data and deletion"), style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = {
                    app.act { app.openUrl(app.engine.run<String>("legal_url", args("page" to "company-data", "lang" to Translator.language))) }
                }) { Text(L("Export data in the portal")) }
                DangerButton(L("Delete company…")) { deleting = true }
            }
        }
    }
    if (inviting) InviteDialog(company) { inviting = false }
    if (renaming) {
        FormDialog(L("Rename company"), fields = listOf(FormField("name", L("Company name"), company.tenant?.name ?: "")), confirm = L("Rename"), onDismiss = { renaming = false }) {
            if (company.rename(it["name"].orEmpty())) null else company.error
        }
    }
    if (deleting) {
        FormDialog(
            L("Delete company"),
            L("Deletes the company at once: devices, invitations, members and their accounts. The subscription ends. Type the company name to confirm."),
            listOf(FormField("name", L("Company name"))), L("Delete company"), danger = true, onDismiss = { deleting = false },
        ) { if (company.deleteCompany(it["name"].orEmpty())) null else company.error }
    }
}

private fun mailState(invite: Invite): String = when (invite.mailState) {
    "sent", "delivered" -> L("E-mail sent")
    "queued", "sending" -> L("E-mail on its way")
    "failed", "bounced" -> L("E-mail failed")
    else -> L("Expires %@", Format.day(invite.expiresAt))
}

@Composable
private fun InviteDialog(company: CompanyModel, onDismiss: () -> Unit) {
    val scope = rememberCoroutineScope()
    var email by remember { mutableStateOf("") }
    var role by remember { mutableStateOf("technician") }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(L("Invite a person")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(L("Mixel Remote e-mails the invitation link. Administrators and technicians count against the technicians of your plan."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(
                    email, { email = it }, label = { Text(L("E-mail")) }, placeholder = { Text("name@company.ch") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth(),
                )
                Text(L("Role"), style = MaterialTheme.typography.labelLarge)
                val roles = listOf("admin", "technician", "member")
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    roles.forEachIndexed { index, item ->
                        SegmentedButton(role == item, { role = item }, SegmentedButtonDefaults.itemShape(index, roles.size)) { Text(Format.role(item), maxLines = 1) }
                    }
                }
                MessageLine(company.error)
            }
        },
        confirmButton = {
            TextButton(enabled = !company.working, onClick = { scope.launch { if (company.invite(email, role)) onDismiss() } }) { Text(L("Send invitation")) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(L("Cancel")) } },
    )
}

@Composable
private fun CreateCompanyCard(app: AppModel, company: CompanyModel) {
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("") }
    var terms by remember { mutableStateOf(false) }
    var personalName by remember { mutableStateOf("") }
    MixelCard {
        CardTitle(L("Create a company"), Icons.Filled.Business)
        Text(L("Your company gets a 14-day Business trial, no card needed. You become its owner and can invite your team."), color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedTextField(name, { name = it }, label = { Text(L("Company name")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.toggleable(terms, role = Role.Checkbox) { terms = it },
        ) {
            Checkbox(terms, onCheckedChange = null)
            Spacer(Modifier.width(8.dp))
            Text(L("I accept the Terms of Service and the Data Processing Agreement"))
        }
        TextButton(onClick = { app.settings.openLegal("legal") }) { Text(L("Read the Terms of Service, the Data Processing Agreement and the Privacy Policy")) }
        PrimaryButton(L("Create company"), enabled = !company.working && name.isNotBlank()) { scope.launch { company.create(name, terms) } }
        if (app.account.account?.hasPersonal == false) {
            HorizontalDivider()
            Text(L("Or a personal space for your own devices"), style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(personalName, { personalName = it }, label = { Text(L("Name shown to your own devices")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedButton(onClick = { scope.launch { company.createPersonalSpace(personalName) } }, enabled = personalName.isNotBlank()) { Text(L("Create personal space")) }
        }
        MessageLine(company.error)
    }
}

@Composable
private fun DangerCard(app: AppModel) {
    var deleting by remember { mutableStateOf(false) }
    MixelCard {
        CardTitle(L("Delete my account"), Icons.Filled.PersonRemove)
        Text(L("Deletes your Mixel Remote account and signs you out on every device. Company data stays with the company."), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        DangerButton(L("Delete my account…")) { deleting = true }
    }
    if (deleting) {
        FormDialog(
            L("Delete my account"), L("Type your e-mail address to confirm. This cannot be undone."),
            listOf(FormField("email", L("E-mail"), keyboard = KeyboardType.Email)), L("Delete my account"), danger = true, onDismiss = { deleting = false },
        ) { if (app.company.deleteAccount(it["email"].orEmpty())) null else app.company.error }
    }
}
