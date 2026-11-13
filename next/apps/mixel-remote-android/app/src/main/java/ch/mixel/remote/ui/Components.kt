package ch.mixel.remote.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import ch.mixel.remote.R
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L
import kotlinx.coroutines.launch

/** The Mixel mark (the brand ring) on its white tile. */
@Composable
fun MixelMark(size: Dp = 28.dp) {
    Image(
        painter = painterResource(R.drawable.mixel_mark),
        contentDescription = null,
        modifier = Modifier
            .size(size)
            .clip(RoundedCornerShape(size * 0.22f))
            .background(Color.White)
            .border(0.5.dp, Color.Black.copy(alpha = 0.08f), RoundedCornerShape(size * 0.22f)),
    )
}

/** A rounded panel on the page background (Mixel's card). */
@Composable
fun MixelCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.medium,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer, contentColor = MaterialTheme.colorScheme.onSurface),
        border = BorderStroke(0.5.dp, Mixel.colors.line),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}

/** The title of a card with an optional icon. */
@Composable
fun CardTitle(text: String, icon: ImageVector? = null) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.semantics { heading() }) {
        if (icon != null) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(8.dp))
        }
        Text(text, style = MaterialTheme.typography.titleMedium)
    }
}

enum class BadgeKind { Online, Offline, Unknown, Busy, Attention }

/** A coloured dot with a word, for online/offline and similar states. */
@Composable
fun StatusBadge(kind: BadgeKind, text: String) {
    val color = when (kind) {
        BadgeKind.Online -> Mixel.colors.success
        BadgeKind.Offline -> MaterialTheme.colorScheme.onSurfaceVariant
        BadgeKind.Unknown -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
        BadgeKind.Busy -> Mixel.colors.warning
        BadgeKind.Attention -> MaterialTheme.colorScheme.primary
    }
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.clearAndSetSemantics { contentDescription = text }) {
        Spacer(Modifier.size(8.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(6.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** An error or notice line (nothing when empty). */
@Composable
fun MessageLine(text: String, error: Boolean = true) {
    if (text.isEmpty()) return
    val color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant
    Row(verticalAlignment = Alignment.Top, modifier = Modifier.semantics(mergeDescendants = true) {}) {
        Icon(if (error) Icons.Filled.Warning else Icons.Filled.Info, contentDescription = null, tint = color, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(6.dp))
        Text(text, color = color, style = MaterialTheme.typography.bodyMedium)
    }
}

/** The main action of a screen: filled Mixel red. */
@Composable
fun PrimaryButton(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Button(onClick = onClick, enabled = enabled, modifier = modifier, shape = MaterialTheme.shapes.small) {
        Text(text, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
fun DangerButton(text: String, enabled: Boolean = true, onClick: () -> Unit) {
    TextButton(onClick = onClick, enabled = enabled, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text(text) }
}

/** A small caption above a field. */
@Composable
fun FieldLabel(text: String) {
    Text(text.uppercase(ch.mixel.remote.support.Translator.locale), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** The 9-digit ID field: groups digits as typed. */
@Composable
fun RemoteIdField(value: String, onChange: (String) -> Unit, modifier: Modifier = Modifier) {
    OutlinedTextField(
        value = value,
        onValueChange = { onChange(Format.groupDigits(it)) },
        label = { Text(L("Partner connection ID")) },
        placeholder = { Text("123 456 789") },
        singleLine = true,
        textStyle = MaterialTheme.typography.titleLarge,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = modifier.fillMaxWidth(),
    )
}

/** One text field of a [FormDialog]. */
data class FormField(val id: String, val label: String, val value: String = "", val keyboard: KeyboardType = KeyboardType.Text)

/**
 * A dialog with text fields (rename, invite, delete with a typed name). `run` returns an error
 * message, or null when done (the dialog then closes).
 */
@Composable
fun FormDialog(
    title: String,
    text: String = "",
    fields: List<FormField>,
    confirm: String,
    danger: Boolean = false,
    onDismiss: () -> Unit,
    run: suspend (Map<String, String>) -> String?,
) {
    val values = remember { mutableStateListOf(*fields.map { it.value }.toTypedArray()) }
    var error by remember { mutableStateOf("") }
    var working by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (text.isNotEmpty()) Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant)
                fields.forEachIndexed { index, field ->
                    OutlinedTextField(
                        value = values[index],
                        onValueChange = { values[index] = it },
                        label = { Text(field.label) },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = field.keyboard),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                MessageLine(error)
            }
        },
        confirmButton = {
            TextButton(
                enabled = !working,
                colors = if (danger) ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error) else ButtonDefaults.textButtonColors(),
                onClick = {
                    working = true
                    scope.launch {
                        val problem = run(fields.mapIndexed { index, field -> field.id to values[index].trim() }.toMap())
                        working = false
                        if (problem == null) onDismiss() else error = problem
                    }
                },
            ) { Text(confirm) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(L("Cancel")) } },
    )
}

/** A yes/no question before something that cannot be undone. */
@Composable
fun ConfirmDialog(title: String, text: String, confirm: String, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(text) },
        confirmButton = {
            TextButton(onClick = { onDismiss(); onConfirm() }, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text(confirm) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(L("Cancel")) } },
    )
}
