package ch.mixel.remote

import android.graphics.Bitmap
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import ch.mixel.remote.app.AppModel
import ch.mixel.remote.demo.DemoFixtures
import ch.mixel.remote.support.Translator
import ch.mixel.remote.ui.LocalDemo
import ch.mixel.remote.ui.MixelTheme
import ch.mixel.remote.ui.RootScreen
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Every screen in light and dark (and German, French, Italian for two of them), on the scripted
 * engine: checks the TalkBack labels and writes screenshots to the app's files/screenshots folder
 * (pulled by scripts/screenshots.sh).
 */
@RunWith(AndroidJUnit4::class)
class ScreensTest {
    @get:Rule val compose = createComposeRule()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val out = File(context.filesDir, "screenshots").apply { mkdirs() }

    @After
    fun tearDown() {
        scope.cancel()
        Translator.choice = "auto"
    }

    private data class Shown(val app: AppModel, val dark: Boolean, val screen: String)
    private val shown = androidx.compose.runtime.mutableStateOf<Shown?>(null)
    private var started = false

    private fun show(screen: String, dark: Boolean, language: String = "en"): AppModel {
        Translator.choice = language
        val app = runBlocking(Dispatchers.Main) { DemoFixtures.screen(screen, scope) }
        if (!started) {
            started = true
            compose.setContent {
                val current = shown.value
                if (current != null) {
                    androidx.compose.runtime.key(current) {
                        MixelTheme(dark = current.dark) {
                            CompositionLocalProvider(LocalDemo provides current.screen) { RootScreen(current.app, startInSettings = current.screen == "settings") }
                        }
                    }
                }
            }
        }
        compose.runOnIdle { shown.value = Shown(app, dark, screen) }
        compose.waitForIdle()
        Thread.sleep(500)
        compose.waitForIdle()
        return app
    }

    private fun shoot(name: String) {
        // The whole screen, dialogs included.
        val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        File(out, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    /** Every clickable element has a name TalkBack can read. */
    private fun assertLabelled() {
        val unnamed = compose.onAllNodes(
            SemanticsMatcher("clickable without a name") { node ->
                val config = node.config
                config.contains(androidx.compose.ui.semantics.SemanticsActions.OnClick) &&
                    !config.contains(SemanticsProperties.ContentDescription) &&
                    !config.contains(SemanticsProperties.Text) &&
                    node.children.none { child -> child.config.contains(SemanticsProperties.Text) || child.config.contains(SemanticsProperties.ContentDescription) }
            },
        ).fetchSemanticsNodes()
        assertTrue("unnamed controls: ${unnamed.map { it.config }}", unnamed.isEmpty())
    }

    private fun all(screen: String, check: () -> Unit = {}) {
        for (dark in listOf(false, true)) {
            show(screen, dark)
            check()
            assertLabelled()
            shoot("$screen-${if (dark) "dark" else "light"}")
        }
    }

    @Test fun signedOut() = all("signed-out") {
        for (button in listOf("Continue with Google", "Continue with Microsoft", "Continue with Apple", "Sign in with Mixel ID")) compose.onNodeWithText(button).assertIsDisplayed()
    }
    @Test fun secondStep() = all("second-step") { compose.onNodeWithText("Enter the code from the authenticator entry “Mixel ISM staff”.").assertIsDisplayed() }
    @Test fun devices() = all("devices") {
        compose.onNodeWithText("Reception PC").assertIsDisplayed()
        compose.onAllNodes(hasText("Offline")).fetchSemanticsNodes().isNotEmpty()
    }
    @Test fun connect() = all("connect") { compose.onNodeWithText("Connect", useUnmergedTree = true) }
    @Test fun setup() = all("setup") { compose.onNodeWithText("Add this phone").assertIsDisplayed() }
    @Test fun account() = all("account") { compose.onNodeWithText("Muster Treuhand AG").assertIsDisplayed() }
    @Test fun settings() = all("settings") { compose.onNodeWithText("Language").assertIsDisplayed() }
    @Test fun help() = all("help") { compose.onNodeWithText("Help someone on their phone", useUnmergedTree = true) }
    @Test fun session() = all("session") { compose.onAllNodes(hasContentDescription("Remote screen")).fetchSemanticsNodes().let { assertTrue(it.isNotEmpty()) } }
    @Test fun chat() = all("chat") { compose.onNodeWithText("Hello, the printer shows an error.").assertIsDisplayed() }
    @Test fun files() = all("files") { compose.onNodeWithText("Receive").assertIsDisplayed() }

    @Test
    fun translations() {
        for ((language, word) in listOf("de" to "Geräte", "fr" to "Appareils", "it" to "Dispositivi")) {
            show("devices", dark = false, language = language)
            compose.onAllNodes(hasText(word)).fetchSemanticsNodes().let { assertTrue(language, it.isNotEmpty()) }
            shoot("devices-$language")
            show("account", dark = true, language = language)
            shoot("account-$language-dark")
        }
    }

    @Test
    fun sessionMenuOffersTheComputersActions() {
        show("session", dark = false)
        compose.onNode(hasContentDescription("More")).performClick()
        compose.waitForIdle()
        for (item in listOf("Ctrl+Alt+Del", "Lock computer", "Restart computer", "Send my clipboard", "Chat", "Files", "Renew connection")) {
            compose.onNodeWithText(item, substring = true).assertIsDisplayed()
        }
        shoot("session-menu-light")
    }
}
