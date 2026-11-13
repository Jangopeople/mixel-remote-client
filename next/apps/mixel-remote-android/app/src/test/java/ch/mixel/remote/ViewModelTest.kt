package ch.mixel.remote

import ch.mixel.remote.app.AppPreferences
import ch.mixel.remote.app.DevicesModel
import ch.mixel.remote.app.EnrollmentModel
import ch.mixel.remote.app.SessionModel
import ch.mixel.remote.app.VideoPacket
import ch.mixel.remote.demo.DemoFixtures
import ch.mixel.remote.engine.EngineFailure
import ch.mixel.remote.engine.EngineJson
import ch.mixel.remote.engine.FakeEngine
import ch.mixel.remote.engine.message
import ch.mixel.remote.model.InputEvent
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.KeyMap
import ch.mixel.remote.support.L
import ch.mixel.remote.support.Translator
import java.io.File
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/** The view models against the scripted engine (same cases as the Apple apps' tests, plus Android's). */
@OptIn(ExperimentalCoroutinesApi::class)
class ViewModelTest {
    @Before
    fun setUp() {
        Translator.load(File("src/main/assets/texts.json").inputStream(), File("src/main/assets/engine-texts.json").inputStream())
        Translator.store = Translator.ChoiceStore.Memory("en")
    }

    private fun FakeEngine.Call.str(name: String) = json[name]?.jsonPrimitive?.content
    private fun events(engine: FakeEngine): List<JsonObject> = engine.sentInput.map { EngineJson.parseToJsonElement(it).jsonObject }
    /** Lets the launched work run (the engine loops never end, so not "until idle"). */
    private fun TestScope.settle() {
        runCurrent()
        advanceTimeBy(2_000)
        runCurrent()
    }

    // Connecting

    @Test
    fun connectBringsThisPhoneOnlineThenRequests() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine, phase = "offline")
        val app = DemoFixtures.app(engine, backgroundScope)
        engine.handle("remote_register") {
            engine.reply("remote_status", """{"phase":"ready","host":482913557}""")
            "null"
        }
        app.connectSteps(731204118, "123456", unattended = false)
        val commands = engine.calls.map { it.command }.filter { it in setOf("remote_register", "quick_phone_claim", "remote_request") }
        assertEquals(listOf("remote_register", "quick_phone_claim", "remote_request"), commands.takeLast(3))
        val request = engine.calls("remote_request").last().json
        assertEquals(731204118L, request["host"]!!.jsonPrimitive.long)
        assertEquals("123456", request["password"]!!.jsonPrimitive.content)
        assertEquals(false, request["unattended"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun phoneIsClaimedAndWaitedForBeforeTheRequest() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        engine.reply("quick_phone_claim", "true")
        engine.reply("remote_status", """{"phase":"ready","host":482913557,"online":[648493658]}""")
        val app = DemoFixtures.app(engine, backgroundScope)
        app.connectSteps(648493658, null, unattended = true)
        assertEquals(false, engine.calls("remote_request").last().json["unattended"]!!.jsonPrimitive.boolean)
        assertTrue(app.progress.contains("Waiting for the other person"))
    }

    @Test
    fun staffOnlyPhoneClaimFailureExplainsTheSecondStep() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        engine.fail("quick_phone_claim", "permission denied: 42501 staff")
        val app = DemoFixtures.app(engine, backgroundScope)
        try {
            app.connectSteps(648493658, null, false)
            fail("must fail")
        } catch (failure: EngineFailure) {
            assertTrue(message(failure).contains("second confirmation step"))
        }
        assertTrue(engine.calls("remote_request").isEmpty())
    }

    @Test
    fun goesOnlineBySelfButNotAfterGoOffline() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine, phase = "offline")
        val app = DemoFixtures.app(engine, backgroundScope)
        assertEquals("a signed-in, set-up phone goes online by itself", 1, engine.calls("remote_register").size)
        app.wentOffline = true
        app.goOnlineIfNeeded()
        assertEquals(1, engine.calls("remote_register").size)
    }

    @Test
    fun missingIdentityAsksForSetupInsteadOfAnError() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine, phase = "offline")
        engine.fail("remote_register", "No saved device identity. Choose Create device identity to generate one locally.")
        val app = DemoFixtures.app(engine, backgroundScope)
        assertTrue(app.enrollment.needsSetup)
        assertEquals("", app.error)
        assertEquals(EnrollmentModel.Path.Company, app.enrollment.path)
    }

    @Test
    fun signInWithAProviderUsesTheAppCallbackAndTheCustomTab() = runTest {
        val engine = FakeEngine()
        DemoFixtures.signedOut(engine)
        engine.reply("account_providers", """["keycloak","google","apple"]""")
        engine.reply("account_sign_in", "\"https://ism-api.mixel.ch/auth/v1/authorize?provider=google\"")
        val app = DemoFixtures.app(engine, backgroundScope)
        app.loadProviders()
        assertEquals("portal order, only what the service offers", listOf("google", "apple", "keycloak"), app.providers)
        var opened = ""
        app.openSignIn = { opened = it }
        app.signIn("google")
        settle()
        val call = engine.calls("account_sign_in").last()
        assertEquals("ch.mixel.remote://auth/callback", call.str("app_callback"))
        assertEquals("google", call.str("provider"))
        app.completeSignIn("ch.mixel.remote://auth/callback?state=s&code=c")
        settle()
        assertEquals("ch.mixel.remote://auth/callback?state=s&code=c", engine.calls("account_callback").last().str("url"))
        assertEquals(false, call.json["open_browser"]!!.jsonPrimitive.boolean)
        assertTrue(opened.startsWith("https://ism-api.mixel.ch/"))
    }

    @Test
    fun secondStepValidatesTheCode() = runTest {
        val engine = FakeEngine()
        DemoFixtures.mfa(engine)
        val app = DemoFixtures.app(engine, backgroundScope)
        assertEquals("mfa_required", app.account.phase)
        app.verificationCode = "12 34"
        app.verify("f")
        assertEquals("Enter the 6-digit code", app.error)
        app.verificationCode = "123 456"
        app.verify("f0000000-0000-4000-8000-000000000001")
        settle()
        assertEquals("123456", engine.calls("account_verify").last().str("code"))
    }

    // Devices

    @Test
    fun deviceStatusAndReasons() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        val app = DemoFixtures.app(engine, backgroundScope)
        val devices = app.devices
        assertEquals(4, devices.devices.size)
        val reception = devices.devices.first { it.name == "Reception PC" }
        val warehouse = devices.devices.first { it.name == "Warehouse laptop" }
        val me = devices.devices.first { it.name == "Anna’s Pixel" }
        assertEquals(DevicesModel.Presence.Online, devices.presence(reception))
        assertEquals("checked once while connected and not online", DevicesModel.Presence.Offline, devices.presence(warehouse))
        assertTrue(devices.reason(warehouse), devices.reason(warehouse).contains("Last seen 3 hours ago"))
        assertEquals(DevicesModel.Presence.This, devices.presence(me))
        assertEquals("This is the device you are using.", devices.connectBlocked(me))
        assertEquals("This device is offline.", devices.connectBlocked(warehouse))
        assertEquals("", devices.connectBlocked(reception))
        assertTrue(devices.manages)
        assertTrue(devices.groupsOn)
        assertEquals(1, devices.groups.size)
        val hosts = engine.calls("remote_presence").last().json["hosts"]!!.jsonArray.map { it.jsonPrimitive.long }
        assertFalse("this phone is not asked about", 482913557L in hosts)
    }

    @Test
    fun devicesWithoutConnectionUseTheServiceRecord() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine, phase = "error")
        engine.fail("remote_register", "Network unreachable")
        val app = DemoFixtures.app(engine, backgroundScope)
        val warehouse = app.devices.devices.first { it.name == "Warehouse laptop" }
        val reception = app.devices.devices.first { it.name == "Reception PC" }
        assertEquals(DevicesModel.Presence.Offline, app.devices.presence(warehouse))
        assertEquals(DevicesModel.Presence.Unknown, app.devices.presence(reception))
        assertEquals("Shown once this phone is connected.", app.devices.reason(reception))
    }

    @Test
    fun manageRenameMovesAndRemovesWithTheTenant() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        engine.reply("saas_rename_device", "\"Front desk\"")
        val app = DemoFixtures.app(engine, backgroundScope)
        val device = app.devices.devices.first()
        assertTrue(app.devices.rename(device, " Front desk "))
        val rename = engine.calls("saas_rename_device").last()
        assertEquals(DemoFixtures.TENANT, rename.str("tenant"))
        assertEquals(device.device, rename.str("device"))
        assertEquals("Front desk", rename.str("name"))
        app.devices.move(device, null)
        assertTrue(engine.calls("saas_move_device").last().args.contains("\"group\":null"))
        app.devices.remove(device)
        assertEquals(1, engine.calls("saas_remove_device").size)
    }

    @Test
    fun deployLinkIsShownOnce() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        engine.reply("saas_create_deploy_link", """{"token":"t","windows_command":"irm x | iex","macos_command":"curl m","linux_command":"curl l"}""")
        val app = DemoFixtures.app(engine, backgroundScope)
        assertTrue(app.devices.deploys)
        assertTrue(app.devices.createDeployLink("Office", 7, 10, null))
        assertEquals("irm x | iex", app.devices.deployLink?.windowsCommand)
        assertEquals(7, engine.calls("saas_create_deploy_link").last().json["days"]!!.jsonPrimitive.int)
    }

    @Test
    fun readOnlyCompanyBlocksAdding() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        engine.reply("saas_tenant_overview", """{"role":"owner","status":"read_only","plan":"business","features":{"groups":true},"limits":{"devices":2000,"technicians":15},"usage":{"devices":4,"technicians":2}}""")
        val app = DemoFixtures.app(engine, backgroundScope)
        assertEquals("This company is read-only", app.devices.addBlocked)
        assertTrue(app.company.readOnly)
    }

    @Test
    fun wakeUsesTheAddressesOfAnEarlierSession() = runTest {
        val engine = FakeEngine()
        DemoFixtures.viewing(engine)
        val preferences = AppPreferences.Memory()
        val app = DemoFixtures.app(engine, backgroundScope, preferences)
        val reception = app.devices.devices.first { it.name == "Reception PC" }
        assertEquals(listOf("525400a1b2c3"), app.devices.macs(reception))
        app.devices.wake(reception)
        settle()
        assertEquals("525400a1b2c3", engine.calls("wake_on_lan").last().json["macs"]!!.jsonArray.first().jsonPrimitive.content)
        assertTrue(app.devices.message.startsWith("Wake signal sent to Reception PC"))
    }

    // Session

    @Test
    fun sessionShowsPictureAndSendsInput() = runTest {
        val engine = FakeEngine()
        DemoFixtures.viewing(engine)
        engine.queue(VideoPacket.sample(320, 200, 1))
        val app = DemoFixtures.app(engine, backgroundScope)
        settle()
        val session = app.session
        assertNotNull(session.picture)
        assertEquals(320, session.width)
        assertTrue(session.controllable)
        session.click(0.5f, 0.25f)
        session.press(6, listOf(KeyMap.CONTROL))
        session.scroll(0, -3)
        settle()
        val sent = events(engine)
        assertEquals("pointer", sent.first()["kind"]!!.jsonPrimitive.content)
        assertEquals(32768, sent.first()["x"]!!.jsonPrimitive.int)
        assertEquals(16384, sent.first()["y"]!!.jsonPrimitive.int)
        assertTrue(sent.any { it["kind"]!!.jsonPrimitive.content == "key" && it["usage"]!!.jsonPrimitive.int == 224 && it["down"]!!.jsonPrimitive.boolean })
        assertTrue(sent.any { it["kind"]!!.jsonPrimitive.content == "button_click" && it["button"]!!.jsonPrimitive.int == 1 })
        assertTrue(sent.any { it["kind"]!!.jsonPrimitive.content == "wheel" && it["vertical"]!!.jsonPrimitive.int == -3 })
        session.clear()
    }

    @Test
    fun rightClickUsesButtonTwo() = runTest {
        val engine = FakeEngine()
        DemoFixtures.viewing(engine)
        engine.queue(VideoPacket.sample())
        val app = DemoFixtures.app(engine, backgroundScope)
        settle()
        app.session.click(0.1f, 0.1f, button = 2)
        app.session.doubleClick(0.2f, 0.2f)
        settle()
        val clicks = events(engine).filter { it["kind"]!!.jsonPrimitive.content == "button_click" }
        assertTrue("protocol: 1 left, 2 right, 3 middle", clicks.any { it["button"]!!.jsonPrimitive.int == 2 })
        assertTrue(clicks.any { it["clicks"]!!.jsonPrimitive.int == 2 })
        app.session.clear()
    }

    @Test
    fun toolbarActionsUseTheDesktopNames() = runTest {
        val engine = FakeEngine()
        DemoFixtures.viewing(engine)
        engine.queue(VideoPacket.sample())
        val app = DemoFixtures.app(engine, backgroundScope)
        settle()
        val session = app.session
        session.togglePrivacy(); session.toggleBlockInput(); session.toggleLockWhenDone(); session.lockNow(); session.ctrlAltDel(); session.toggleSound(); session.selectDisplay(1)
        settle()
        val sent = events(engine)
        for (name in listOf("privacy:on", "block:on", "lockend:on", "lock", "sas", "sound:off")) {
            assertTrue(name, sent.any { it["kind"]!!.jsonPrimitive.content == "action" && it["name"]!!.jsonPrimitive.content == name })
        }
        assertTrue(sent.any { it["kind"]!!.jsonPrimitive.content == "select_display" && it["index"]!!.jsonPrimitive.int == 1 })
        session.clear()
    }

    @Test
    fun restartArmsThenRestartsAndReconnects() = runTest {
        val engine = FakeEngine()
        DemoFixtures.viewing(engine)
        engine.queue(VideoPacket.sample())
        val app = DemoFixtures.app(engine, backgroundScope)
        settle()
        app.session.restartComputer()
        assertTrue(app.session.restartArmed)
        app.session.restartComputer()
        settle()
        assertTrue(events(engine).any { it["name"]?.jsonPrimitive?.content == "restart" })
        // The computer is gone, then back online: the app asks for the session again.
        engine.reply("remote_status", """{"phase":"ready","host":482913557,"online":[]}""")
        app.refreshRemote()
        assertTrue(app.session.reconnecting)
        assertTrue(app.session.shown)
        engine.reply("remote_status", """{"phase":"ready","host":482913557,"online":[731204118]}""")
        app.refreshRemote()
        settle()
        assertEquals(731204118L, engine.calls("remote_request").last().json["host"]!!.jsonPrimitive.long)
        app.session.stopReconnecting()
        app.session.clear()
    }

    @Test
    fun endSessionUsesTheConsentAndStopsThePicture() = runTest {
        val engine = FakeEngine()
        DemoFixtures.viewing(engine)
        engine.queue(VideoPacket.sample())
        val app = DemoFixtures.app(engine, backgroundScope)
        settle()
        assertNotNull(app.session.picture)
        app.session.end()
        assertNull(app.session.picture)
        settle()
        assertEquals(DemoFixtures.CONSENT, engine.calls("remote_stop_session").last().str("consent"))
    }

    @Test
    fun pointerMovesCoalesce() = runTest {
        val engine = FakeEngine()
        DemoFixtures.viewing(engine)
        engine.queue(VideoPacket.sample())
        val app = DemoFixtures.app(engine, backgroundScope)
        settle()
        for (step in 0 until 50) app.session.move(step / 50f, 0.5f)
        settle()
        assertTrue("unsent moves are replaced by the newest", engine.sentInput.size < 50)
        assertEquals(65535, SessionModel.normalized(1.5f))
        assertEquals(0, SessionModel.normalized(-1f))
        app.session.clear()
    }

    @Test
    fun hardwareDecoderGetsCompressedPicturesAndFallsBack() = runTest {
        val engine = FakeEngine()
        val compressed = ByteArray(40).also { bytes ->
            "MRH1".forEachIndexed { index, c -> bytes[index] = c.code.toByte() }
            bytes[4] = 1; bytes[16] = 1
        }
        engine.queue(compressed)
        engine.queue(VideoPacket.sample(64, 32, 2))
        val decoded = mutableListOf<VideoPacket.Compressed>()
        DemoFixtures.company(engine)
        val app = DemoFixtures.app(engine, backgroundScope)
        app.session.decoder = object : ch.mixel.remote.app.HardwareDecoder {
            override fun decode(packet: VideoPacket.Compressed): Boolean { decoded += packet; return false }
            override val pictureWidth = 0
            override val pictureHeight = 0
            override fun reset() {}
        }
        DemoFixtures.viewing(engine)
        app.refreshRemote()
        settle()
        assertEquals(1, decoded.size)
        assertTrue(decoded.first().keyframe)
        assertEquals("after the decoder failed, the engine's pictures are shown", 64, app.session.picture?.width)
        app.session.clear()
    }

    @Test
    fun chatFilesAndClipboard() = runTest {
        val engine = FakeEngine()
        DemoFixtures.viewing(engine)
        val app = DemoFixtures.app(engine, backgroundScope)
        app.session.readTools()
        assertEquals(2, app.session.chat.size)
        assertEquals("driver-setup.exe", app.session.files?.outgoing?.name)
        assertEquals(31f / 48f, app.session.files!!.outgoing!!.progress, 0.001f)
        app.session.chatDraft = "  Done  "
        app.session.sendChat()
        app.session.acceptFile("f2000000-0000-4000-8000-000000000002", "/data/received/scan-0042.pdf")
        app.session.clipboard(push = true)
        settle()
        assertEquals("Done", engine.calls("remote_chat_send").last().str("text"))
        assertEquals("/data/received/scan-0042.pdf", engine.calls("remote_file_accept").last().str("path"))
        assertEquals("/data/received/scan-0042.pdf", app.session.received["f2000000-0000-4000-8000-000000000002"])
        assertEquals(true, engine.calls("remote_clipboard").last().json["push"]!!.jsonPrimitive.boolean)
        assertEquals("Remote clipboard updated.", app.session.clipboardResult)
        app.session.clear()
    }

    // Company

    @Test
    fun companyPlanUsageAndInvite() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        engine.reply("saas_invite_member", "\"44444444-4444-4444-8444-444444444444\"")
        val app = DemoFixtures.app(engine, backgroundScope)
        assertEquals("Muster Treuhand AG", app.company.tenant?.name)
        assertTrue(app.company.planLine, app.company.planLine.startsWith("Business trial until"))
        assertEquals("4 of 2,000 devices · 2 of 15 technicians", app.company.usageLine)
        assertEquals(2, app.company.members.size)
        assertEquals(1, app.company.invites.size)
        assertFalse(app.company.invite("not-an-address", "technician"))
        assertTrue(app.company.invite(" lea@muster.test ", "admin"))
        val invite = engine.calls("saas_invite_member").last()
        assertEquals("lea@muster.test", invite.str("email"))
        assertEquals("admin", invite.str("role"))
        assertEquals("en", invite.str("locale"))
    }

    @Test
    fun creatingACompanyNeedsTheTerms() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        val app = DemoFixtures.app(engine, backgroundScope)
        assertFalse(app.company.create("New AG", acceptedTerms = false))
        assertTrue(engine.calls("saas_signup").isEmpty())
    }

    @Test
    fun selfRemovalForgetsThisPhone() = runTest {
        val engine = FakeEngine()
        DemoFixtures.company(engine)
        engine.reply("device_self_remove", "\"removed\"")
        val preferences = AppPreferences.Memory()
        val app = DemoFixtures.app(engine, backgroundScope, preferences)
        app.enrollment.remember("d1", 482913557, 3, DemoFixtures.TENANT)
        assertNotNull(preferences.get(EnrollmentModel.KEY))
        assertEquals(ch.mixel.remote.model.SelfRemoval.removed, app.enrollment.removeThisDevice())
        assertNull(preferences.get(EnrollmentModel.KEY))
        val registration = engine.calls("device_self_remove").last().json["registration"]!!.jsonObject
        assertEquals(3, registration["generation"]!!.jsonPrimitive.int)
    }

    // Pictures, translations, keys

    @Test
    fun videoFrameChecksHeaderAndSequence() {
        val frame = VideoPacket.decode(VideoPacket.sample(64, 32, 5)) as VideoPacket.Picture
        assertEquals(64, frame.width)
        assertEquals(5, frame.sequence)
        assertThrows { VideoPacket.decode(VideoPacket.sample(64, 32, 5), after = 5) }
        val broken = VideoPacket.sample(64, 32).also { it[0] = 0 }
        assertThrows { VideoPacket.decode(broken) }
        val oversized = VideoPacket.sample(64, 32).also { it[4] = 0; it[5] = 0x10 } // width 4096
        assertThrows { VideoPacket.decode(oversized) }
    }

    private fun assertThrows(work: () -> Unit) {
        try { work(); fail("must fail") } catch (_: VideoPacket.Invalid) {}
    }

    @Test
    fun engineAndScreenTextsAreTranslated() {
        Translator.choice = "de"
        assertEquals("Verbindung annehmen", Translator.translate("Accept connection"))
        assertEquals("Geräte", L("Devices"))
        assertEquals("A name nobody translated", Translator.translate("A name nobody translated"))
        assertEquals("Verbindung annehmen", EngineFailure("Accept connection").translated)
        assertEquals("Mit Reception PC verbinden", L("Connect to %@", "Reception PC"))
        Translator.choice = "fr"
        assertNotEquals("Remote control", L("Remote control"))
        Translator.choice = "it"
        assertNotEquals("Devices", L("Devices"))
        Translator.choice = "auto"
        Translator.systemLanguage = { "fr" }
        assertEquals("fr", Translator.language)
        Translator.systemLanguage = { "ja" }
        assertEquals("en", Translator.language)
        Translator.choice = "en"
    }

    @Test
    fun inputEventsEncodeLikeTheDesktopApp() {
        assertEquals("""{"kind":"release_all"}""", InputEvent.ReleaseAll.json().toString())
        assertEquals("""{"kind":"wheel","horizontal":0,"vertical":-3}""", InputEvent.Wheel(0, -3).json().toString())
        assertEquals("""{"kind":"button_click","button":3,"down":true,"clicks":1}""", InputEvent.ButtonClick(3, true, 1).json().toString())
        assertEquals("""{"kind":"text","text":"é"}""", InputEvent.Text("é").json().toString())
    }

    @Test
    fun androidKeysMapToHidUsages() {
        assertEquals(4, KeyMap.usage(android.view.KeyEvent.KEYCODE_A))
        assertEquals(29, KeyMap.usage(android.view.KeyEvent.KEYCODE_Z))
        assertEquals(30, KeyMap.usage(android.view.KeyEvent.KEYCODE_1))
        assertEquals(39, KeyMap.usage(android.view.KeyEvent.KEYCODE_0))
        assertEquals(40, KeyMap.usage(android.view.KeyEvent.KEYCODE_ENTER))
        assertEquals(42, KeyMap.usage(android.view.KeyEvent.KEYCODE_DEL))
        assertEquals(58, KeyMap.usage(android.view.KeyEvent.KEYCODE_F1))
        assertEquals(69, KeyMap.usage(android.view.KeyEvent.KEYCODE_F12))
        assertEquals(89, KeyMap.usage(android.view.KeyEvent.KEYCODE_NUMPAD_1))
        assertEquals(224, KeyMap.usage(android.view.KeyEvent.KEYCODE_CTRL_LEFT))
        assertNull(KeyMap.usage(android.view.KeyEvent.KEYCODE_VOLUME_UP))
        assertEquals(6, KeyMap.usage('C'))
    }

    @Test
    fun textBecomesKeysWhenTheComputerCannotTypeText() = runTest {
        val engine = FakeEngine().apply { refuseText = true }
        DemoFixtures.viewing(engine)
        engine.queue(VideoPacket.sample())
        val app = DemoFixtures.app(engine, backgroundScope)
        settle()
        app.session.type("Hi!")
        app.session.type("x")
        settle()
        val keys = events(engine).filter { it["kind"]!!.jsonPrimitive.content == "key" }.map { it["usage"]!!.jsonPrimitive.int to it["down"]!!.jsonPrimitive.boolean }
        // H (Shift+11), i (12), ! (Shift+30), then x (27) directly as keys.
        assertEquals(listOf(225 to true, 11 to true, 11 to false, 225 to false, 12 to true, 12 to false, 225 to true, 30 to true, 30 to false, 225 to false, 27 to true, 27 to false), keys)
        assertEquals("only the first text was tried as text", 1, events(engine).count { it["kind"]!!.jsonPrimitive.content == "text" })
        assertEquals("", app.session.error)
        assertNull(KeyMap.press('é'))
        app.session.clear()
    }

    @Test
    fun remoteIdParsing() {
        assertEquals(731204118L, Format.parseRemoteId("731 204 118"))
        assertNull(Format.parseRemoteId("031204118"))
        assertNull(Format.parseRemoteId("73120411"))
        assertNull(Format.parseRemoteId("73120411x"))
        assertEquals("731 204 118", Format.remoteId(731204118))
        assertEquals("— — —", Format.remoteId(null))
        assertEquals("731 204 11", Format.groupDigits("7312041a1"))
    }
}
