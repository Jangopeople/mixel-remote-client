package ch.mixel.remote.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ch.mixel.remote.engine.args
import ch.mixel.remote.engine.message
import ch.mixel.remote.engine.perform
import ch.mixel.remote.engine.run
import ch.mixel.remote.model.SelfRemoval
import ch.mixel.remote.model.StartupSettings
import ch.mixel.remote.model.UpdateInfo
import ch.mixel.remote.support.Translator
import kotlinx.coroutines.launch

/** Settings: stay signed in, language, this phone (remove it), updates, About and legal. */
class SettingsModel(private val app: AppModel) {
    var startup by mutableStateOf<StartupSettings?>(null)
        private set
    var update by mutableStateOf<UpdateInfo?>(null)
        private set
    var updateChecked by mutableStateOf(false)
        private set
    var removalPending by mutableStateOf(false)
        private set
    var language by mutableStateOf(Translator.choice)
        private set
    var error by mutableStateOf("")

    suspend fun load() {
        startup = runCatching { app.engine.run<StartupSettings>("startup_settings") }.getOrNull()
        removalPending = runCatching { app.engine.run<Boolean>("device_removal_pending") }.getOrDefault(false)
    }

    /** Sends a removal of this phone that was saved while offline. */
    suspend fun flushPendingRemoval() {
        val status = runCatching { app.engine.run<SelfRemoval?>("device_self_remove_flush") }.getOrNull()
        if (status != null && status != SelfRemoval.queued) app.enrollment.forget()
        removalPending = runCatching { app.engine.run<Boolean>("device_removal_pending") }.getOrDefault(false)
    }

    fun setStaySignedIn(enabled: Boolean) = change { app.engine.perform("set_stay_signed_in", args("enabled" to enabled)) }

    fun chooseLanguage(value: String) {
        language = value
        Translator.choice = value
    }

    /** Asks store.mixel.ch whether a newer Android download exists (Google Play updates by itself). */
    fun checkForUpdate(variant: String) = change {
        update = app.engine.run("update_check", args("variant" to variant))
        updateChecked = true
    }

    fun openLegal(page: String) = change {
        val url: String = app.engine.run("legal_url", args("page" to page, "lang" to Translator.language))
        app.openUrl(url)
    }

    private fun change(work: suspend () -> Unit) {
        error = ""
        app.scope.launch {
            try {
                work()
            } catch (failure: Exception) {
                error = message(failure)
            }
            load()
        }
    }
}
