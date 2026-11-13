package ch.mixel.remote.engine

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/**
 * A scripted engine for the view-model tests, the screenshot tests and the demo screens. Each
 * command answers with the JSON set for it (or `null`); every call is recorded.
 */
class FakeEngine(platform: String = "android", controllerOnly: Boolean = true) : EngineApi {
    data class Call(val command: String, val args: String) {
        val json: JsonObject get() = runCatching { EngineJson.parseToJsonElement(args).jsonObject }.getOrElse { JsonObject(emptyMap()) }
    }

    private val lock = Any()
    private val replies = mutableMapOf<String, Result<String>>()
    private val handlers = mutableMapOf<String, (String) -> String>()
    private val recorded = mutableListOf<Call>()
    private val frames = ArrayDeque<ByteArray>()
    private val inputs = mutableListOf<String>()
    override var info = EngineInfo("2.0.0", platform, controllerOnly)
    /** Answer text input with "not applied", as a Linux (X11) computer does. */
    var refuseText = false

    /** Answer `command` with this JSON. */
    fun reply(command: String, json: String) = synchronized(lock) { replies[command] = Result.success(json) }

    fun fail(command: String, message: String) = synchronized(lock) { replies[command] = Result.failure(EngineFailure(message)) }

    /** A reply computed from the arguments (throw [EngineFailure] to fail). */
    fun handle(command: String, handler: (String) -> String) = synchronized(lock) { handlers[command] = handler }

    fun queue(frame: ByteArray) = synchronized(lock) { frames.addLast(frame) }

    val calls: List<Call> get() = synchronized(lock) { recorded.toList() }
    fun calls(command: String): List<Call> = calls.filter { it.command == command }
    val sentInput: List<String> get() = synchronized(lock) { inputs.toList() }

    override suspend fun call(command: String, args: String): String {
        val (reply, handler) = synchronized(lock) {
            recorded.add(Call(command, args))
            replies[command] to handlers[command]
        }
        if (handler != null) return handler(args)
        return (reply ?: Result.success("null")).getOrThrow()
    }

    override suspend fun nextVideo(session: String, compressed: Boolean): ByteArray =
        synchronized(lock) { frames.removeFirstOrNull() } ?: ByteArray(0)

    override suspend fun input(session: String, event: String): Boolean {
        synchronized(lock) { inputs.add(event) }
        return !(refuseText && event.contains("\"kind\":\"text\""))
    }

    override suspend fun shutdown() {
        synchronized(lock) { recorded.add(Call("shutdown", "{}")) }
    }
}
