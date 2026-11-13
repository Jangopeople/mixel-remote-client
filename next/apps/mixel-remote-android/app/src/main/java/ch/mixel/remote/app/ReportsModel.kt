package ch.mixel.remote.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ch.mixel.remote.engine.EngineFailure
import ch.mixel.remote.engine.args
import ch.mixel.remote.engine.message
import ch.mixel.remote.engine.perform
import ch.mixel.remote.engine.run
import ch.mixel.remote.model.SaasDevice
import ch.mixel.remote.model.RemoteStatus
import ch.mixel.remote.model.SessionRow
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.launch

/**
 * Session reports of the company (SQL 029): who connected to which device, when and for how long,
 * with notes and CSV/PDF export. Owners and admins see every session and filter by technician;
 * technicians and members see their own. The same rules and file layout as the desktop app.
 */
class ReportsModel(private val app: AppModel, private val zone: () -> ZoneId = ZoneId::systemDefault) {
    enum class State { Idle, Loading, Ok, Plan, Missing, Error }

    var from by mutableStateOf("")
    var to by mutableStateOf("")
    /** Technician filter (user id; "" = all). Owners and admins only. */
    var technician by mutableStateOf("")
    /** Device filter (device id; "" = all). */
    var device by mutableStateOf("")
    var rows by mutableStateOf<List<SessionRow>>(emptyList())
        private set
    var state by mutableStateOf(State.Idle)
        private set
    var problem by mutableStateOf("")
        private set
    var notice by mutableStateOf("")
    var devices by mutableStateOf<List<SaasDevice>>(emptyList())
        private set

    /** The session that ended last, offered a note (its computer's Remote ID), or null. */
    var notePrompt by mutableStateOf<NotePrompt?>(null)
        private set

    data class NotePrompt(val connection: Long, val name: String)

    val tenant get() = app.company.tenant
    val manager: Boolean get() = app.company.role in setOf("owner", "admin")
    /** The company's plan has session reports (every paid plan; trials run on Business). */
    val included: Boolean get() = app.company.overview?.features?.get("reports") ?: true

    init {
        defaultPeriod(LocalDate.now(zone()))
    }

    fun defaultPeriod(today: LocalDate) {
        from = today.minusDays(30).toString()
        to = today.toString()
    }

    /** The period as the service expects it: start of the first day to the start of the day after the last (local). */
    fun period(): Pair<String, String> {
        val first = runCatching { LocalDate.parse(from) }.getOrNull()
        val last = runCatching { LocalDate.parse(to) }.getOrNull()
        if (first == null || last == null || first.isAfter(last)) throw EngineFailure(L("Choose a start date on or before the end date."))
        val start = first.atStartOfDay(zone()).toInstant()
        val end = last.plusDays(1).atStartOfDay(zone()).toInstant()
        if (Duration.between(start, end) > Duration.ofDays(366).plusHours(2)) throw EngineFailure(L("Choose a period of at most 366 days."))
        return start.toString() to end.toString()
    }

    suspend fun load() {
        val tenant = tenant ?: run { rows = emptyList(); state = State.Idle; return }
        state = State.Loading
        problem = ""
        try {
            val (start, end) = period()
            if (devices.isEmpty()) devices = runCatching { app.engine.run<List<SaasDevice>>("saas_devices", args("tenant" to tenant.tenantId)) }.getOrDefault(emptyList())
            rows = app.engine.run(
                "saas_session_report",
                args(
                    "tenant" to tenant.tenantId, "from" to start, "to" to end,
                    "technician" to technician.ifEmpty { null }?.takeIf { manager }, "device" to device.ifEmpty { null },
                ),
            )
            state = State.Ok
        } catch (failure: Exception) {
            rows = emptyList()
            val text = (failure as? EngineFailure)?.text ?: failure.message.orEmpty()
            state = when {
                Regex("session reports need a paid plan", RegexOption.IGNORE_CASE).containsMatchIn(text) -> State.Plan
                Regex("command .*not found|unknown command|could not find the function|does not exist|PGRST202|42883", RegexOption.IGNORE_CASE).containsMatchIn(text) -> State.Missing
                else -> State.Error
            }
            problem = if (state == State.Error) message(failure) else ""
        }
    }

    fun reload() { app.scope.launch { load() } }

    /** Owners and admins edit any note; the others the notes of their own sessions. */
    fun canEditNote(row: SessionRow): Boolean = manager || row.technicianId == null || row.technicianId == app.account.account?.profile

    suspend fun saveNote(row: SessionRow, text: String): Boolean {
        val tenant = tenant ?: return false
        val note = text.trim()
        if (note == (row.note ?: "")) return true
        return try {
            app.engine.run<String>("saas_session_note", args("tenant" to tenant.tenantId, "note" to note, "session" to row.sessionId))
            rows = rows.map { if (it.sessionId == row.sessionId) it.copy(note = note.ifEmpty { null }) else it }
            notice = if (note.isEmpty()) L("Note removed.") else L("Note saved.")
            true
        } catch (failure: Exception) {
            problem = message(failure)
            false
        }
    }

    // After a session: an optional note (this phone controlled a company device, the plan has reports).

    private var current: Pair<String, Long>? = null

    fun statusChanged(next: RemoteStatus) {
        val controlling = next.phase == "active" && !next.sharing && next.session != null && next.peer != null
        val running = current
        if (running != null && (next.phase == "closing" || (controlling && next.session == running.first))) return
        current = if (controlling) next.session!! to next.peer!! else null
        if (running != null && running.first != current?.first) offerNote(running.second)
    }

    private fun offerNote(host: Long) {
        if (tenant == null || !included || notePrompt != null) return
        val device = app.devices.devices.firstOrNull { it.connectionId == host }
            ?: devices.firstOrNull { it.connectionId == host }?.device
            ?: return
        notePrompt = NotePrompt(host, device.name)
    }

    fun dismissNote() { notePrompt = null }

    suspend fun saveSessionNote(text: String): Boolean {
        val prompt = notePrompt ?: return true
        val tenant = tenant ?: return true
        val note = text.trim()
        if (note.isEmpty()) { notePrompt = null; return true }
        return try {
            app.engine.run<String>("saas_session_note", args("tenant" to tenant.tenantId, "note" to note, "connection" to prompt.connection))
            notePrompt = null
            notice = L("Note saved.")
            true
        } catch (failure: Exception) {
            problem = message(failure)
            false
        }
    }

    // Totals and export

    fun seconds(row: SessionRow, now: Instant = Instant.now()): Long {
        row.durationSeconds?.takeIf { it >= 0 }?.let { return it }
        val start = Format.parseDate(row.startedAt) ?: return 0
        val end = Format.parseDate(row.endedAt) ?: now
        return maxOf(0, Duration.between(start, end).seconds)
    }

    val totalSeconds: Long get() = rows.sumOf { seconds(it) }

    /** "12 sessions · total 3:04:05" */
    val summary: String
        get() = "${rows.size} ${if (rows.size == 1) L("session") else L("sessions")} · ${L("total")} ${duration(totalSeconds)}"

    fun stamp(iso: String?): String {
        val at = Format.parseDate(iso) ?: return ""
        return DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(zone()).format(at)
    }

    fun kindLabel(row: SessionRow): String {
        val kind = if (row.kind == "unattended") L("Unattended") else L("Attended")
        return if (row.colleague == true) "$kind · ${L("Joined as colleague")}" else kind
    }

    /** "mixel-remote-<company>-sessions-<from>-<to>.<ext>" */
    fun fileName(extension: String): String {
        val slug = (tenant?.slug ?: "").replace(Regex("[^a-zA-Z0-9-]"), "").lowercase().ifEmpty { "company" }
        return "mixel-remote-$slug-sessions-$from-$to.$extension"
    }

    /** UTF-8 with BOM, RFC 4180 quoting, formula-looking cells neutralised (as the desktop app and the portal). */
    fun csv(now: Instant = Instant.now()): String {
        val header = listOf(
            L("Technician"), L("Email"), L("Device"), L("Connection ID"), L("Start"), L("End"), L("Duration"),
            L("Duration (seconds)"), L("Type"), L("Joined as colleague"), L("Note"), L("Session ID"),
        )
        val lines = mutableListOf(header.joinToString(",") { cell(it) })
        for (row in rows) {
            val seconds = seconds(row, now)
            lines += listOf(
                row.technicianName ?: "", row.technicianEmail ?: "", row.deviceName ?: "", row.connectionId?.toString() ?: "",
                stamp(row.startedAt), if (row.endedAt != null) stamp(row.endedAt) else L("Running"), duration(seconds), seconds.toString(),
                if (row.kind == "unattended") L("Unattended") else L("Attended"), if (row.colleague == true) L("Yes") else L("No"),
                row.note ?: "", row.sessionId,
            ).joinToString(",") { cell(it) }
        }
        return "\uFEFF" + lines.joinToString("\r\n") + "\r\n"
    }

    companion object {
        /** 3725 → "1:02:05" */
        fun duration(seconds: Long): String {
            val total = maxOf(0, seconds)
            return "%d:%02d:%02d".format(total / 3600, (total % 3600) / 60, total % 60)
        }

        fun cell(value: String): String {
            var text = value
            if (text.isNotEmpty() && text[0] in "=+-@\t\r") text = "'$text"
            return if (text.any { it == '"' || it == ',' || it == '\r' || it == '\n' }) "\"" + text.replace("\"", "\"\"") + "\"" else text
        }
    }
}
