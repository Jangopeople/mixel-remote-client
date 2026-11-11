package ch.mixel.quicksupport

import java.nio.ByteBuffer

/** The host core (Rust, `crates/quick_host`). */
object Native {
    interface Listener {
        /** Called from the core's own threads with one event as JSON text. */
        fun onEvent(json: String)
    }

    init {
        System.loadLibrary("quick_host")
    }

    @JvmStatic external fun create(seed: ByteArray?, listener: Listener): Long
    @JvmStatic external fun seed(handle: Long): ByteArray?
    /** Blocks. Returns the registration as JSON; throws RuntimeException with a readable reason. */
    @JvmStatic external fun enroll(handle: Long, code: String, name: String): String
    @JvmStatic external fun start(handle: Long, enrollment: String, width: Int, height: Int, control: Boolean)
    @JvmStatic external fun decide(handle: Long, accept: Boolean)
    @JvmStatic external fun endSession(handle: Long)
    @JvmStatic external fun stop(handle: Long)
    @JvmStatic external fun pushFrame(handle: Long, buffer: ByteBuffer, offset: Int, length: Int)
    @JvmStatic external fun destroy(handle: Long)
}
