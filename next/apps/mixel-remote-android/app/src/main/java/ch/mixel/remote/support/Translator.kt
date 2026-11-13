package ch.mixel.remote.support

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import java.io.InputStream
import java.util.Locale
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Mixel Remote in English, German, French or Italian: the phone's language, or the one chosen in
 * Settings. Screen texts are English keys (`L(key)`) looked up in `texts.json` (made by
 * `scripts/make-strings.py` from the Apple apps' String Catalog, the desktop app's tables and the
 * Android-only texts). Engine messages are translated with the desktop app's tables
 * (`engine-texts.json`). Device names, chat messages and typed values are never translated.
 */
object Translator {
    val supported = listOf("en", "de", "fr", "it")

    @Serializable
    private class Table(val texts: Map<String, String> = emptyMap(), val patterns: List<List<String>> = emptyList())

    private var screen: Map<String, Map<String, String>> = emptyMap()
    private var engine: Map<String, Table> = emptyMap()
    private val compiled = mutableMapOf<String, List<Pair<Regex, String>>>()

    /** Where the choice is kept (the app's preferences; memory in tests). */
    var store: ChoiceStore = ChoiceStore.Memory()

    /** Changes whenever the language changes, so the screens draw again. */
    var revision by mutableIntStateOf(0)
        private set

    /** The phone's language (tests set it). */
    var systemLanguage: () -> String = { Locale.getDefault().language }

    fun load(screenTexts: InputStream, engineTexts: InputStream) {
        val json = Json { ignoreUnknownKeys = true }
        screen = json.decodeFromString(screenTexts.bufferedReader().use { it.readText() })
        engine = json.decodeFromString(engineTexts.bufferedReader().use { it.readText() })
        synchronized(compiled) { compiled.clear() }
        revision++
    }

    /** `auto`, `en`, `de`, `fr` or `it`. */
    var choice: String
        get() = store.get() ?: "auto"
        set(value) {
            store.set(if (value == "auto") null else value)
            revision++
        }

    /** The language shown now. */
    val language: String
        get() {
            val chosen = choice
            if (chosen != "auto" && chosen in supported) return chosen
            val system = systemLanguage().take(2).lowercase()
            return if (system in supported) system else "en"
        }

    val locale: Locale get() = Locale.forLanguageTag(language)

    /** An engine text in the shown language (unknown texts stay English). */
    fun translate(text: String, into: String = language): String {
        val core = text.trim()
        if (into == "en" || core.isEmpty() || core.length > 600) return text
        val table = engine[into] ?: return text
        table.texts[core]?.let { return text.replace(core, it) }
        for ((pattern, target) in patterns(into, table)) {
            if (pattern.containsMatchIn(core)) return text.replace(core, pattern.replace(core, target))
        }
        return text
    }

    private fun patterns(language: String, table: Table): List<Pair<Regex, String>> = synchronized(compiled) {
        compiled.getOrPut(language) {
            table.patterns.mapNotNull { pair ->
                if (pair.size != 2) null else runCatching { Regex(pair[0]) to pair[1] }.getOrNull()
            }
        }
    }

    /** A screen text: the screen table first, then the engine's tables, else English. */
    fun screenText(key: String): String {
        val language = language
        if (language != "en") screen[language]?.get(key)?.let { return it }
        return translate(key, language)
    }

    interface ChoiceStore {
        fun get(): String?
        fun set(value: String?)

        class Memory(private var value: String? = null) : ChoiceStore {
            override fun get() = value
            override fun set(value: String?) { this.value = value }
        }
    }
}

/**
 * A screen text in the shown language. `%@` (text) and `%d` (number) placeholders are filled from
 * `args`, as in the Apple apps' texts.
 */
fun L(key: String, vararg args: Any): String {
    @Suppress("UNUSED_VARIABLE") val redraw = Translator.revision
    val text = Translator.screenText(key)
    if (args.isEmpty()) return text
    return try {
        // `%@` and `%d` both take the value as written (numbers are formatted by the caller where needed).
        String.format(Translator.locale, text.replace("%@", "%s").replace("%d", "%s"), *args.map { it.toString() }.toTypedArray())
    } catch (_: Exception) {
        text
    }
}
