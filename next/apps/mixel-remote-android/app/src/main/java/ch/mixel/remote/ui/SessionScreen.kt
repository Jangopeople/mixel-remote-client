package ch.mixel.remote.ui

import androidx.core.graphics.createBitmap
import android.graphics.SurfaceTexture
import android.view.HapticFeedbackConstants
import android.view.KeyCharacterMap
import android.view.Surface
import android.view.TextureView
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.KeyboardHide
import androidx.compose.material.icons.filled.Monitor
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.app.SessionModel
import ch.mixel.remote.app.VideoPacket
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.KeyMap
import ch.mixel.remote.support.L
import java.nio.ByteBuffer
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * A session: the remote screen with zoom, touch control and the keyboard. Tap = click, double tap =
 * double click, touch and hold = right click, two fingers = scroll, pinch = zoom (this phone only),
 * one finger moves a zoomed picture. The key bar adds Esc, Tab, arrows and held Ctrl/Alt/Win.
 */
@Composable
fun SessionScreen(app: AppModel, platform: Platform) {
    val session = app.session
    val remote = app.remote
    var keyboard by remember { mutableStateOf(false) }
    val held = remember { mutableStateListOf<Int>() }
    val demo = LocalDemo.current
    var showChat by remember { mutableStateOf(demo == "chat") }
    var showFiles by remember { mutableStateOf(demo == "files") }
    var confirmEnd by remember { mutableStateOf(false) }
    BackHandler { if (keyboard) keyboard = false else confirmEnd = true }
    Column(Modifier.fillMaxSize().background(Color.Black).statusBarsPadding().navigationBarsPadding().imePadding()) {
        TopBar(app, keyboard, onKeyboard = { keyboard = !keyboard }, onChat = { showChat = true }, onFiles = { showFiles = true }, onEnd = { confirmEnd = true })
        Box(Modifier.weight(1f).fillMaxWidth()) {
            RemotePicture(session, held)
            if (!session.hasPicture) {
                Column(Modifier.align(Alignment.Center).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    CircularProgressIndicator(color = Color.White)
                    Text(
                        if (remote.phase == "outgoing") L("Waiting for the other person to accept your request.") else L("Connecting securely…"),
                        color = Color.White.copy(alpha = 0.85f),
                    )
                    OutlinedButton(onClick = { if (session.reconnecting && !remote.inSession) session.stopReconnecting() else session.end() }) { Text(L("Cancel"), color = Color.White) }
                }
            }
            val line = session.error.ifEmpty { session.notice }.ifEmpty { session.clipboardResult }
            if (line.isNotEmpty()) {
                Text(
                    line, color = Color.White, style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.align(Alignment.BottomCenter).padding(16.dp).background(Color.Black.copy(alpha = 0.7f), RoundedCornerShape(10.dp)).padding(10.dp),
                )
            }
            if (keyboard && session.controllable) KeyCatcher(session, held)
        }
        if (remote.control) KeyBar(session, held)
    }
    if (showChat) ChatSheet(session) { showChat = false }
    if (showFiles) FilesSheet(session, platform) { showFiles = false }
    if (confirmEnd) {
        ConfirmDialog(L("End session?"), L("The connection to %@ ends for both sides.", Format.remoteId(remote.peer)), L("End session"), onDismiss = { confirmEnd = false }) { session.end() }
    }
}

@Composable
private fun TopBar(app: AppModel, keyboard: Boolean, onKeyboard: () -> Unit, onChat: () -> Unit, onFiles: () -> Unit, onEnd: () -> Unit) {
    val session = app.session
    val remote = app.remote
    var menu by remember { mutableStateOf(false) }
    var displays by remember { mutableStateOf(false) }
    Surface(color = MaterialTheme.colorScheme.surfaceContainer, contentColor = MaterialTheme.colorScheme.onSurface) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(Format.remoteId(remote.peer), style = MaterialTheme.typography.titleMedium, fontFamily = FontFamily.Monospace)
                Text(
                    (if (remote.control) L("View and control") else L("View only")) + if (session.fps > 0) " · ${session.fps} fps" else "",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (remote.control && remote.displays > 1) {
                Box {
                    IconButton(onClick = { displays = true }) { Icon(Icons.Filled.Monitor, contentDescription = L("Display")) }
                    DropdownMenu(displays, { displays = false }) {
                        for (index in 0 until remote.displays) {
                            val label = if (remote.displayAll && index == remote.displays - 1) L("All screens") else L("Display %d", index + 1)
                            DropdownMenuItem({ Text(label) }, { displays = false; session.selectDisplay(index) })
                        }
                    }
                }
            }
            if (remote.control) {
                IconButton(onClick = onKeyboard) {
                    Icon(if (keyboard) Icons.Filled.KeyboardHide else Icons.Filled.Keyboard, contentDescription = if (keyboard) L("Hide keyboard") else L("Keyboard"))
                }
            }
            Box {
                IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, contentDescription = L("More")) }
                DropdownMenu(menu, { menu = false }) {
                    fun item(text: String, action: () -> Unit) = @Composable { DropdownMenuItem({ Text(text) }, { menu = false; action() }) }
                    if (remote.offers("sas")) item(L("Ctrl+Alt+Del")) { session.ctrlAltDel() }()
                    if (remote.offers("lock")) item(L("Lock computer")) { session.lockNow() }()
                    if (remote.offers("lockend")) item("${L("Lock when the session ends")}: ${if (session.lockWhenDone) L("on") else L("off")}") { session.toggleLockWhenDone() }()
                    if (remote.offers("privacy")) item("${L("Privacy screen")}: ${if (session.privacy) L("on") else L("off")}") { session.togglePrivacy() }()
                    if (remote.offers("block")) item("${L("Block input")}: ${if (session.blockInput) L("on") else L("off")}") { session.toggleBlockInput() }()
                    if (remote.offers("sound")) item("${L("Sound")}: ${if (session.sound) L("on") else L("off")}") { session.toggleSound() }()
                    if (remote.offers("restart")) item(if (session.restartArmed) L("Click again to restart") else L("Restart computer")) { session.restartComputer() }()
                    if (remote.phase == "active" && remote.clipboard) {
                        item(L("Send my clipboard")) { session.clipboard(push = true) }()
                        item(L("Get the remote clipboard")) { session.clipboard(push = false) }()
                    }
                    if (remote.phase == "active") item(L("Chat")) { onChat() }()
                    if (remote.phase == "active" && remote.files) item(L("Files")) { onFiles() }()
                    item(L("Renew connection")) { session.renew() }()
                }
            }
            IconButton(onClick = onEnd) { Icon(Icons.Filled.Close, contentDescription = L("End session"), tint = MaterialTheme.colorScheme.primary) }
        }
    }
}

/** The remote picture: the phone's decoder on a texture, or the engine's pictures; zoom and touch. */
@Composable
private fun RemotePicture(session: SessionModel, held: MutableList<Int>) {
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    var box by remember { mutableStateOf(IntSize.Zero) }
    val view = LocalView.current
    val aspect = if (session.width > 1 && session.height > 1) session.width.toFloat() / session.height else 16f / 10f

    // Where the picture is drawn inside the box (fitted, then zoomed and moved).
    fun pictureRect(): Pair<Offset, androidx.compose.ui.geometry.Size> {
        val w = box.width.toFloat(); val h = box.height.toFloat()
        val fitW = min(w, h * aspect); val fitH = fitW / aspect
        val center = Offset(w / 2, h / 2)
        val size = androidx.compose.ui.geometry.Size(fitW * scale, fitH * scale)
        val topLeft = center + offset - Offset(size.width / 2, size.height / 2)
        return topLeft to size
    }

    fun toPicture(point: Offset): Pair<Float, Float>? {
        val (topLeft, size) = pictureRect()
        if (size.width <= 0f || size.height <= 0f) return null
        val x = (point.x - topLeft.x) / size.width
        val y = (point.y - topLeft.y) / size.height
        return if (x in 0f..1f && y in 0f..1f) x to y else null
    }

    fun clampOffset() {
        val w = box.width.toFloat(); val h = box.height.toFloat()
        val fitW = min(w, h * aspect); val fitH = fitW / aspect
        val maxX = max(0f, (fitW * scale - w) / 2) + 0f
        val maxY = max(0f, (fitH * scale - h) / 2) + 0f
        offset = Offset(offset.x.coerceIn(-maxX, maxX), offset.y.coerceIn(-maxY, maxY))
    }

    BoxWithConstraints(
        Modifier
            .fillMaxSize()
            .semantics {
                contentDescription = L("Remote screen")
                stateDescription = if (session.controllable) L("Tap to click, touch and hold for a right click, two fingers to scroll, pinch to zoom.") else L("View only")
            }
            .pointerInput(session.controllable) {
                detectTapGestures(
                    onTap = { point -> if (session.controllable) toPicture(point)?.let { (x, y) -> session.click(x, y) } },
                    onDoubleTap = { point -> if (session.controllable) toPicture(point)?.let { (x, y) -> session.doubleClick(x, y) } },
                    onLongPress = { point ->
                        if (session.controllable) toPicture(point)?.let { (x, y) ->
                            view.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
                            session.click(x, y, button = 2)
                        }
                    },
                )
            }
            .pointerInput(Unit) {
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    var mode = 0 // 0 undecided, 1 zoom, 2 scroll
                    var zoomTotal = 1f
                    var panTotal = Offset.Zero
                    var wheel = Offset.Zero
                    var moved = false
                    do {
                        val event = awaitPointerEvent()
                        val pressed = event.changes.filter { it.pressed }
                        if (pressed.size >= 2) {
                            val zoom = event.calculateZoom()
                            val pan = event.calculatePan()
                            zoomTotal *= zoom
                            panTotal += pan
                            if (mode == 0) {
                                if (abs(zoomTotal - 1f) > 0.08f) mode = 1 else if (panTotal.getDistance() > 24f) mode = 2
                            }
                            if (mode == 1) {
                                val centroid = event.calculateCentroid()
                                val next = (scale * zoom).coerceIn(1f, 5f)
                                val center = Offset(box.width / 2f, box.height / 2f)
                                // Keep the point under the fingers in place.
                                offset = (offset - (centroid - center)) * (next / scale) + (centroid - center) + pan
                                scale = next
                                if (scale == 1f) offset = Offset.Zero
                                clampOffset()
                            } else if (mode == 2 && session.controllable) {
                                if (!moved) { event.calculateCentroid().let { c -> toPicture(c)?.let { (x, y) -> session.move(x, y) } }; moved = true }
                                wheel += pan / 12f
                                val horizontal = wheel.x.toInt(); val vertical = wheel.y.toInt()
                                wheel -= Offset(horizontal.toFloat(), vertical.toFloat())
                                session.scroll(horizontal, vertical)
                            }
                            event.changes.forEach { if (it.positionChanged()) it.consume() }
                        } else if (pressed.size == 1 && scale > 1f) {
                            val change = pressed.first()
                            val delta = change.position - change.previousPosition
                            if (delta.getDistance() > 0f) {
                                offset += delta
                                clampOffset()
                                change.consume()
                            }
                        }
                    } while (event.changes.any { it.pressed })
                }
            },
    ) {
        LaunchedEffect(constraints.maxWidth, constraints.maxHeight) { box = IntSize(constraints.maxWidth, constraints.maxHeight) }
        val (topLeft, size) = pictureRect()
        // The phone's decoder draws onto this texture (kept while the session screen is shown).
        AndroidView(
            factory = { context -> TextureView(context).apply { surfaceTextureListener = DecoderSurface(session) } },
            modifier = Modifier.fillMaxSize().drawPicture(topLeft, size, box),
        )
        val picture = session.picture
        if (picture != null && !session.hardwarePicture) {
            val bitmap = remember(picture.width, picture.height) { createBitmap(picture.width, picture.height) }
            val image = remember(picture.sequence, bitmap) {
                bitmap.copyPixelsFromBuffer(ByteBuffer.wrap(picture.pixels))
                bitmap.asImageBitmap()
            }
            Canvas(Modifier.fillMaxSize()) {
                drawImage(
                    image,
                    dstOffset = androidx.compose.ui.unit.IntOffset(topLeft.x.toInt(), topLeft.y.toInt()),
                    dstSize = androidx.compose.ui.unit.IntSize(size.width.toInt(), size.height.toInt()),
                    filterQuality = androidx.compose.ui.graphics.FilterQuality.Medium,
                )
            }
        }
    }
    // Held modifiers are released when the picture goes away.
    DisposableEffect(Unit) { onDispose { held.clear() } }
}

/** Places the decoder's texture where the picture belongs (fitted, zoomed and moved). */
private fun Modifier.drawPicture(topLeft: Offset, size: androidx.compose.ui.geometry.Size, box: IntSize): Modifier {
    if (box.width == 0 || box.height == 0 || size.width <= 0f) return this
    return this.graphicsLayer {
        transformOrigin = androidx.compose.ui.graphics.TransformOrigin(0f, 0f)
        scaleX = size.width / box.width
        scaleY = size.height / box.height
        translationX = topLeft.x
        translationY = topLeft.y
    }
}

/** Connects the texture's surface to a [H264Decoder] for the session while it exists. */
private class DecoderSurface(private val session: SessionModel) : TextureView.SurfaceTextureListener {
    private var surface: Surface? = null
    private var decoder: H264Decoder? = null

    override fun onSurfaceTextureAvailable(texture: SurfaceTexture, width: Int, height: Int) {
        val target = Surface(texture)
        surface = target
        decoder = H264Decoder(target).also { session.decoder = it }
    }

    override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, width: Int, height: Int) {}

    override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
        if (session.decoder === decoder) session.decoder = null
        decoder?.close()
        decoder = null
        surface?.release()
        surface = null
        return true
    }

    override fun onSurfaceTextureUpdated(texture: SurfaceTexture) {}
}

/**
 * An invisible text field that receives typing: the on-screen keyboard (text, Backspace, Enter) and
 * a hardware keyboard (keys as they are pressed, so shortcuts work).
 */
@Composable
private fun KeyCatcher(session: SessionModel, held: MutableList<Int>) {
    val sentinel = "  "
    var value by remember { mutableStateOf(TextFieldValue(sentinel, TextRange(sentinel.length))) }
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    LaunchedEffect(Unit) { focus.requestFocus(); keyboard?.show() }
    fun typed(text: String) {
        for (character in text) {
            if (character == '\n') { session.press(KeyMap.ENTER, held.toList()); held.clear(); continue }
            if (held.isEmpty()) session.type(character.toString())
            else KeyMap.usage(character)?.let { session.press(it, held.toList()); held.clear() }
        }
    }
    BasicTextField(
        value = value,
        onValueChange = { next ->
            // Send what changed against the text the keyboard knows (it may send several changes
            // before it sees ours), then keep its text; only a short or long buffer starts over.
            val before = value.text
            val now = next.text
            val common = before.commonPrefixWith(now).length
            repeat(before.length - common) { session.press(KeyMap.BACKSPACE, held.toList()) }
            typed(now.substring(common))
            value = if (now.length < sentinel.length || now.length > 400) TextFieldValue(sentinel, TextRange(sentinel.length)) else next
        },
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None, autoCorrectEnabled = false,
            keyboardType = KeyboardType.Password, imeAction = ImeAction.None,
        ),
        textStyle = MaterialTheme.typography.bodySmall.copy(color = Color.Transparent),
        modifier = Modifier
            .size(1.dp)
            .focusRequester(focus)
            .semantics { contentDescription = L("Keyboard for the remote computer") }
            .onPreviewKeyEvent { event ->
                val native = event.nativeKeyEvent
                val usage = KeyMap.usage(native.keyCode) ?: return@onPreviewKeyEvent false
                val hardware = native.deviceId != KeyCharacterMap.VIRTUAL_KEYBOARD && native.device?.isVirtual == false
                if (!hardware && usage != KeyMap.ENTER && usage != KeyMap.BACKSPACE) return@onPreviewKeyEvent false
                when (event.type) {
                    KeyEventType.KeyDown -> if (hardware) session.key(usage, true) else session.press(usage, held.toList())
                    KeyEventType.KeyUp -> if (hardware) session.key(usage, false)
                }
                true
            },
    )
}

/** Modifier keys to hold and special keys. */
@Composable
private fun KeyBar(session: SessionModel, held: MutableList<Int>) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainer) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp, vertical = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("Ctrl" to KeyMap.CONTROL, "Alt" to KeyMap.ALT, "Win/⌘" to KeyMap.COMMAND, "⇧" to KeyMap.SHIFT).forEach { (label, usage) ->
                val on = usage in held
                FilterChip(
                    selected = on,
                    onClick = { if (on) held.remove(usage) else held.add(usage) },
                    label = { Text(label) },
                    colors = FilterChipDefaults.filterChipColors(selectedContainerColor = MaterialTheme.colorScheme.primary, selectedLabelColor = MaterialTheme.colorScheme.onPrimary),
                    modifier = Modifier.semantics {
                        contentDescription = when (usage) { KeyMap.CONTROL -> "Control"; KeyMap.ALT -> "Alt"; KeyMap.COMMAND -> L("Windows or Command key"); else -> "Shift" }
                        stateDescription = if (on) L("held") else L("not held")
                    },
                )
            }
            KeyMap.Special.entries.forEach { key ->
                TextButton(
                    onClick = { session.press(key.usage, held.toList()); held.clear() },
                    modifier = Modifier.background(MaterialTheme.colorScheme.surfaceContainerHighest, RoundedCornerShape(8.dp)).semantics { contentDescription = L(key.spoken) },
                ) { Text(key.label, color = MaterialTheme.colorScheme.onSurface) }
            }
        }
    }
}

@Composable
private fun SessionDialog(title: String, onDismiss: () -> Unit, content: @Composable () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) }, text = content, confirmButton = { TextButton(onClick = onDismiss) { Text(L("Done")) } })
}

@Composable
fun ChatSheet(session: SessionModel, onDismiss: () -> Unit) {
    SessionDialog(L("Chat"), onDismiss) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (session.chat.isEmpty()) Text(L("No messages yet."), color = MaterialTheme.colorScheme.onSurfaceVariant)
            Column(Modifier.androidxVerticalScroll(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                session.chat.forEach { message ->
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (message.mine) Arrangement.End else Arrangement.Start) {
                        Text(
                            message.text,
                            color = if (message.mine) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier
                                .background(if (message.mine) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceContainerHighest, RoundedCornerShape(10.dp))
                                .padding(8.dp)
                                .semantics { contentDescription = (if (message.mine) L("You") else L("Other side")) + ": " + message.text },
                        )
                    }
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                androidx.compose.material3.OutlinedTextField(
                    session.chatDraft, { session.chatDraft = it }, label = { Text(L("Chat message")) }, singleLine = true, modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                PrimaryButton(L("Send"), enabled = session.chatDraft.isNotBlank()) { session.sendChat() }
            }
        }
    }
}

private fun Modifier.androidxVerticalScroll(): Modifier = this

@Composable
fun FilesSheet(session: SessionModel, platform: Platform, onDismiss: () -> Unit) {
    SessionDialog(L("Files"), onDismiss) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            val files = session.files
            files?.outgoing?.let { transfer ->
                Text(L("Sending %@", transfer.name), style = MaterialTheme.typography.titleSmall)
                androidx.compose.material3.LinearProgressIndicator(progress = { transfer.progress }, modifier = Modifier.fillMaxWidth())
                Text("${Format.bytes(transfer.offset)} / ${Format.bytes(transfer.size)} · ${transfer.phase}", style = MaterialTheme.typography.bodySmall)
                if (transfer.phase !in setOf("complete", "failed")) DangerButton(L("Cancel")) { session.cancelFile(transfer.id) }
            }
            files?.incoming?.let { transfer ->
                Text(L("Receiving %@", transfer.name), style = MaterialTheme.typography.titleSmall)
                when (transfer.phase) {
                    "offered" -> Row {
                        PrimaryButton(L("Receive")) { session.acceptFile(transfer.id, platform.receivePath(transfer.name)) }
                        Spacer(Modifier.width(8.dp))
                        DangerButton(L("Decline")) { session.cancelFile(transfer.id) }
                    }
                    "complete" -> session.received[transfer.id]?.let { path ->
                        PrimaryButton(L("Save to this phone…")) { platform.saveReceived(path, transfer.name) }
                    }
                    else -> {
                        androidx.compose.material3.LinearProgressIndicator(progress = { transfer.progress }, modifier = Modifier.fillMaxWidth())
                        Text("${Format.bytes(transfer.offset)} / ${Format.bytes(transfer.size)} · ${transfer.phase}", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            if (files?.message?.isNotEmpty() == true) MessageLine(ch.mixel.remote.support.Translator.translate(files.message), error = false)
            OutlinedButton(onClick = { platform.chooseFileToSend { path -> if (path != null) session.sendFile(path) } }) { Text(L("Send a file…")) }
            MessageLine(session.error)
        }
    }
}
