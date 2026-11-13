package ch.mixel.remote.engine

import ch.mixel.remote.support.Translator
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNamingStrategy
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.serializer

/**
 * What the app needs from the Mixel Remote engine. The real engine (Rust, through UniFFI) and the
 * test engine ([FakeEngine]) both provide it, so every screen and view model runs against either.
 *
 * Commands use the names and arguments of the desktop app (`remote_status`, `saas_members`, …);
 * arguments and results are JSON. The picture and the input have their own calls.
 */
interface EngineApi {
    val info: EngineInfo

    /** Runs one command; `args` is a JSON object, the result is JSON. Throws [EngineFailure]. */
    suspend fun call(command: String, args: String): String

    /** The next session picture ("MRF2" RGBA or "MRH1" H.264), empty when none is waiting. */
    suspend fun nextVideo(session: String, compressed: Boolean): ByteArray

    /** One input event (JSON as the desktop app sends it). `false`: the computer could not apply it. */
    suspend fun input(session: String, event: String): Boolean

    /** Before the app goes away for good. */
    suspend fun shutdown()
}

data class EngineInfo(val version: String, val platform: String, val controllerOnly: Boolean)

/** A command that failed; the message comes from the engine (English) and is shown translated. */
class EngineFailure(val text: String) : Exception(text) {
    val translated: String get() = Translator.translate(text)
}

/** JSON as the engine writes it: snake_case keys, unknown keys ignored. */
@OptIn(ExperimentalSerializationApi::class)
val EngineJson = Json {
    namingStrategy = JsonNamingStrategy.SnakeCase
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
    coerceInputValues = true
}

/** Arguments from plain Kotlin values: `args("session" to id, "push" to true)`. */
fun args(vararg values: Pair<String, Any?>): String = JsonObject(values.associate { (key, value) -> key to element(value) }).toString()

fun element(value: Any?): JsonElement = when (value) {
    null -> JsonNull
    is JsonElement -> value
    is Boolean -> JsonPrimitive(value)
    is Number -> JsonPrimitive(value)
    is String -> JsonPrimitive(value)
    is List<*> -> JsonArray(value.map { element(it) })
    is Map<*, *> -> JsonObject(value.entries.associate { (key, item) -> key.toString() to element(item) })
    else -> error("Unsupported argument ${value::class.simpleName}")
}

/** Runs a command and decodes its result. */
suspend inline fun <reified T> EngineApi.run(command: String, args: String = "{}"): T {
    val reply = call(command, args)
    return decode(serializer(), reply, command)
}

fun <T> decode(serializer: KSerializer<T>, reply: String, command: String): T = try {
    EngineJson.decodeFromString(serializer, reply)
} catch (_: Exception) {
    throw EngineFailure("The app could not read the engine's answer to $command")
}

/** Runs a command whose result is not needed. */
suspend fun EngineApi.perform(command: String, args: String = "{}") {
    call(command, args)
}

/** The message of any failure, translated for the person. */
fun message(error: Throwable): String = when (error) {
    is EngineFailure -> error.translated
    else -> Translator.translate(error.message ?: "Something went wrong")
}
