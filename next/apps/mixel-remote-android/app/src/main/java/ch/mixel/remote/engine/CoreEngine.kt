package ch.mixel.remote.engine

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import uniffi.mixel_core_ffi.EngineException
import uniffi.mixel_core_ffi.MixelEngine
import uniffi.mixel_core_ffi.PhoneClipboard
import uniffi.mixel_core_ffi.SecureStorage
import uniffi.mixel_core_ffi.StorageException
import uniffi.mixel_core_ffi.installAndroid

/** The real engine: the Rust Mixel Remote engine behind the app's [EngineApi]. */
class CoreEngine private constructor(private val engine: MixelEngine) : EngineApi {
    override val info: EngineInfo = engine.info().let { EngineInfo(it.version, it.platform, it.controllerOnly) }

    override suspend fun call(command: String, args: String): String = wrap { engine.call(command, args) }

    override suspend fun nextVideo(session: String, compressed: Boolean): ByteArray = wrap { engine.nextVideo(session, compressed) }

    override suspend fun input(session: String, event: String): Boolean = wrap { engine.input(session, event) }

    override suspend fun shutdown() = wrap { engine.shutdown() }

    companion object {
        @Volatile private var installed = false

        /** One engine per app; its private folder is `files/engine` (never backed up: see the manifest). */
        fun create(context: Context): CoreEngine {
            synchronized(this) {
                if (!installed) {
                    installAndroid(KeystoreStorage(File(context.noBackupFilesDir, "secrets")), AndroidClipboard(context.applicationContext))
                    installed = true
                }
            }
            val folder = File(context.noBackupFilesDir, "engine").apply { mkdirs() }
            return try {
                CoreEngine(MixelEngine(folder.absolutePath))
            } catch (error: EngineException) {
                throw failure(error)
            }
        }

        fun failure(error: Throwable): EngineFailure = when (error) {
            is EngineException -> EngineFailure(error.message ?: "The engine stopped")
            is EngineFailure -> error
            else -> EngineFailure(error.message ?: "The engine stopped")
        }

        private inline fun <T> wrap(work: () -> T): T = try {
            work()
        } catch (error: EngineException) {
            throw failure(error)
        }
    }
}

/**
 * The engine's secrets (device key, remembered sign-in), each sealed with AES-GCM by a key that lives
 * in the Android Keystore and never leaves it. The sealed files are in the app's private folder that
 * is excluded from backups, so a copied backup can never impersonate this phone.
 */
class KeystoreStorage(private val folder: File) : SecureStorage {
    private val alias = "ch.mixel.remote.engine-secrets.v1"

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    private fun file(name: String) = File(folder, "$name.sealed")

    override fun read(name: String): ByteArray? {
        val sealed = try {
            file(name).takeIf { it.exists() }?.readBytes() ?: return null
        } catch (_: Exception) {
            throw StorageException.Failed("The secure storage could not be read")
        }
        if (sealed.size < 13 || sealed[0] != VERSION) throw StorageException.Failed("The stored secret is invalid")
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, sealed, 1, 12))
            cipher.updateAAD(name.toByteArray())
            cipher.doFinal(sealed, 13, sealed.size - 13)
        } catch (_: Exception) {
            throw StorageException.Failed("The stored secret could not be unlocked")
        }
    }

    override fun write(name: String, value: ByteArray) {
        try {
            folder.mkdirs()
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key())
            cipher.updateAAD(name.toByteArray())
            val body = cipher.doFinal(value)
            val iv = cipher.iv
            check(iv.size == 12)
            val temporary = File(folder, "$name.tmp")
            temporary.writeBytes(byteArrayOf(VERSION) + iv + body)
            if (!temporary.renameTo(file(name))) throw IllegalStateException()
        } catch (_: Exception) {
            throw StorageException.Failed("The secure storage could not save the secret")
        }
    }

    override fun remove(name: String) {
        file(name).delete()
    }

    private companion object {
        const val VERSION: Byte = 1
    }
}

/** The phone's clipboard for the engine (text); read and written on the main thread, as Android wants. */
class AndroidClipboard(private val context: Context) : PhoneClipboard {
    private val main = Handler(Looper.getMainLooper())

    private fun <T> onMain(work: () -> T): T? {
        if (Looper.myLooper() == Looper.getMainLooper()) return runCatching(work).getOrNull()
        var result: T? = null
        val done = CountDownLatch(1)
        main.post { result = runCatching(work).getOrNull(); done.countDown() }
        done.await(2, TimeUnit.SECONDS)
        return result
    }

    private val manager get() = context.getSystemService(ClipboardManager::class.java)

    override fun readText(): String? = onMain {
        manager.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString()
    }

    override fun writeText(text: String): Boolean = onMain {
        manager.setPrimaryClip(ClipData.newPlainText("Mixel Remote", text))
        true
    } ?: false
}
