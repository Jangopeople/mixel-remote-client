package ch.mixel.remote.support

import java.text.DateFormat
import java.text.NumberFormat
import java.time.Instant
import java.time.OffsetDateTime
import java.util.Date
import kotlin.math.max
import kotlin.math.roundToLong

object Format {
    /** A Remote ID in groups of three: 123 456 789. */
    fun remoteId(id: Long?): String {
        if (id == null) return "— — —"
        val digits = id.toString()
        if (digits.length != 9) return digits
        return "${digits.substring(0, 3)} ${digits.substring(3, 6)} ${digits.substring(6, 9)}"
    }

    /** A typed Remote ID: nine digits, the first not 0 (spaces allowed). `null` otherwise. */
    fun parseRemoteId(text: String): Long? {
        val digits = text.filterNot { it.isWhitespace() }
        if (digits.length != 9 || !digits.all { it in '0'..'9' } || digits.first() == '0') return null
        return digits.toLong()
    }

    /** The digits typed so far, grouped as 123 456 789 (at most nine). */
    fun groupDigits(text: String): String {
        val digits = text.filter { it in '0'..'9' }.take(9)
        return digits.chunked(3).joinToString(" ")
    }

    fun parseDate(iso: String?): Instant? {
        if (iso.isNullOrBlank()) return null
        return runCatching { OffsetDateTime.parse(iso).toInstant() }.getOrNull()
            ?: runCatching { Instant.parse(iso) }.getOrNull()
            ?: runCatching { OffsetDateTime.parse(iso.replace(" ", "T")).toInstant() }.getOrNull()
    }

    /** "Last seen 5 minutes ago" and similar. */
    fun lastSeen(iso: String?, now: Instant = Instant.now()): String {
        val date = parseDate(iso) ?: return ""
        val minutes = max(0L, ((now.toEpochMilli() - date.toEpochMilli()) / 60_000.0).roundToLong())
        if (minutes < 2) return L("Last seen just now")
        if (minutes < 60) return L("Last seen %d minutes ago", minutes)
        val hours = (minutes / 60.0).roundToLong()
        if (hours < 24) return if (hours == 1L) L("Last seen 1 hour ago") else L("Last seen %d hours ago", hours)
        val days = (hours / 24.0).roundToLong()
        return if (days == 1L) L("Last seen yesterday") else L("Last seen %d days ago", days)
    }

    fun day(iso: String?): String {
        val date = parseDate(iso) ?: return ""
        return DateFormat.getDateInstance(DateFormat.MEDIUM, Translator.locale).format(Date.from(date))
    }

    fun number(value: Int): String = NumberFormat.getIntegerInstance(Translator.locale).format(value)

    fun bytes(count: Long): String {
        val units = listOf("bytes", "KB", "MB", "GB")
        var value = count.toDouble()
        var unit = 0
        while (value >= 1000 && unit < units.size - 1) { value /= 1000; unit++ }
        return if (unit == 0) "$count ${units[0]}" else String.format(Translator.locale, "%.1f %s", value, units[unit])
    }

    /** The operating system as people say it. */
    fun os(os: String): String = when (os.lowercase()) {
        "macos" -> "macOS"
        "windows" -> "Windows"
        "linux" -> "Linux"
        "ios" -> "iPhone or iPad"
        "android" -> "Android"
        "other" -> L("phone or tablet")
        else -> os
    }

    /** The person's role in a company, translated. */
    fun role(role: String): String = when (role) {
        "owner" -> L("Owner")
        "admin" -> L("Administrator")
        "technician" -> L("Technician")
        "member" -> L("Member")
        else -> role
    }

    fun plan(plan: String): String = when (plan) {
        "personal" -> "Personal"
        "solo" -> "Solo"
        "team" -> "Team"
        "business" -> "Business"
        "enterprise" -> "Enterprise"
        else -> plan.replaceFirstChar { it.uppercase() }
    }
}
