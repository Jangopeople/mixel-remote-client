package ch.mixel.remote.ui

import android.app.DatePickerDialog
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Assessment
import androidx.compose.material.icons.filled.EditNote
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.app.ReportsModel
import ch.mixel.remote.model.SessionRow
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L
import java.time.LocalDate
import kotlinx.coroutines.launch

/** Session reports: period, technician (owners and admins), device; the sessions with notes; CSV and PDF. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ReportsScreen(app: AppModel, platform: Platform) {
    val model = app.reports
    val scope = rememberCoroutineScope()
    var editing by remember { mutableStateOf<SessionRow?>(null) }
    LaunchedEffect(app.company.tenant?.tenantId) { model.load() }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            MixelCard {
                CardTitle(L("Session reports"), Icons.Filled.Assessment)
                Text(
                    if (model.manager) L("Every remote session of your company: who connected to which device, when and for how long.")
                    else L("Your remote sessions: which device, when and for how long."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                when {
                    model.tenant == null -> MessageLine(L("Sign in with a Mixel Remote company account to see session reports."), error = false)
                    model.state == ReportsModel.State.Plan || !model.included ->
                        MessageLine(L("Session reports need a paid plan. They are included in the Solo, Team, Business and Enterprise plans."), error = false)
                    model.state == ReportsModel.State.Missing -> MessageLine(L("Session reports are not available yet. Please try again later."), error = false)
                    else -> Filters(app, model, platform)
                }
            }
        }
        if (model.state == ReportsModel.State.Loading) {
            item { Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(28.dp)) } }
        }
        if (model.state == ReportsModel.State.Ok) {
            item { Text(model.summary + if (model.rows.size >= 5000) " · " + L("Only the newest 5000 sessions are shown. Choose a shorter period to see all of them.") else "", style = MaterialTheme.typography.titleSmall) }
            if (model.rows.isEmpty()) item { Text(L("No sessions in this period."), color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(model.rows, key = { it.sessionId }) { row -> SessionCard(model, row) { editing = row } }
        }
        item { MessageLine(model.notice, error = false); MessageLine(model.problem) }
    }
    editing?.let { row ->
        NoteDialog(
            title = L("Session note"), subtitle = "${row.deviceName ?: ""} · ${model.stamp(row.startedAt)}", initial = row.note ?: "",
            hint = L("Up to 4000 characters. Leave it empty to remove the note."), label = L("Note"), skip = L("Cancel"),
            onDismiss = { editing = null },
        ) { text -> scope.launch { if (model.saveNote(row, text)) editing = null } }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Filters(app: AppModel, model: ReportsModel, platform: Platform) {
    val context = LocalContext.current
    fun pick(current: String, set: (String) -> Unit) {
        val day = runCatching { LocalDate.parse(current) }.getOrDefault(LocalDate.now())
        DatePickerDialog(context, { _, y, m, d -> set(LocalDate.of(y, m + 1, d).toString()) }, day.year, day.monthValue - 1, day.dayOfMonth).show()
    }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = { pick(model.from) { model.from = it } }, modifier = Modifier.semantics { contentDescription = L("From") + " " + Format.day(model.from + "T12:00:00Z") }) {
            Text(L("From") + ": " + Format.day(model.from + "T12:00:00Z"))
        }
        OutlinedButton(onClick = { pick(model.to) { model.to = it } }, modifier = Modifier.semantics { contentDescription = L("To") + " " + Format.day(model.to + "T12:00:00Z") }) {
            Text(L("To") + ": " + Format.day(model.to + "T12:00:00Z"))
        }
    }
    if (model.manager) {
        Choice(
            L("Technician"), model.technician,
            listOf("" to L("All technicians")) + app.company.members.sortedBy { it.name.lowercase() }.map { it.userId to it.name.ifEmpty { it.email } },
        ) { model.technician = it }
    }
    Choice(
        L("Device"), model.device,
        listOf("" to L("All devices")) + model.devices.filter { it.revoked != true }.sortedBy { it.name.lowercase() }.map { it.deviceId to it.name },
    ) { model.device = it }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        PrimaryButton(L("Show"), enabled = model.state != ReportsModel.State.Loading) { model.reload() }
        val ready = model.state == ReportsModel.State.Ok && model.rows.isNotEmpty()
        OutlinedButton(onClick = { platform.saveReport(model.fileName("csv"), "text/csv", model.csv().toByteArray(Charsets.UTF_8)) }, enabled = ready) { Text(L("Export CSV")) }
        OutlinedButton(onClick = { platform.saveReport(model.fileName("pdf"), "application/pdf", ReportPdf.build(model, app.company.tenant?.name ?: "")) }, enabled = ready) { Text(L("Export PDF")) }
    }
}

@Composable
private fun Choice(label: String, value: String, options: List<Pair<String, String>>, onChoose: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { open = true }, modifier = Modifier.fillMaxWidth().semantics { contentDescription = "$label: " + (options.firstOrNull { it.first == value }?.second ?: "") }) {
            Text("$label: " + (options.firstOrNull { it.first == value }?.second ?: options.first().second), modifier = Modifier.weight(1f))
        }
        DropdownMenu(open, { open = false }) {
            for ((id, name) in options) DropdownMenuItem({ Text(name) }, { open = false; onChoose(id) })
        }
    }
}

@Composable
private fun SessionCard(model: ReportsModel, row: SessionRow, onEdit: () -> Unit) {
    MixelCard {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(row.deviceName ?: "", fontWeight = FontWeight.SemiBold)
                Text("${model.stamp(row.startedAt)} → ${if (row.endedAt != null) model.stamp(row.endedAt) else L("Running")}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("${row.technicianName ?: row.technicianEmail ?: "—"} · ${model.kindLabel(row)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(ReportsModel.duration(model.seconds(row)), style = MaterialTheme.typography.titleSmall)
        }
        row.note?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        if (model.canEditNote(row)) {
            TextButton(onClick = onEdit, modifier = Modifier.semantics { contentDescription = (if (row.note != null) L("Edit note") else L("Add note")) + ": " + (row.deviceName ?: "") }) {
                Icon(Icons.Filled.EditNote, contentDescription = null, modifier = Modifier.size(18.dp))
                Text(if (row.note != null) L("Edit note") else L("Add note"), modifier = Modifier.padding(start = 6.dp))
            }
        }
    }
}

/** A note with Save and Skip/Cancel (after a session, or editing a report row). */
@Composable
fun NoteDialog(title: String, subtitle: String, initial: String, hint: String, label: String, skip: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(text, { if (it.length <= 4000) text = it }, label = { Text(label) }, minLines = 3, modifier = Modifier.fillMaxWidth())
                Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = { TextButton(onClick = { onSave(text) }) { Text(L("Save note")) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(skip) } },
    )
}
