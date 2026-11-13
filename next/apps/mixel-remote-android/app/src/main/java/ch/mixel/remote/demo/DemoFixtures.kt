package ch.mixel.remote.demo

import ch.mixel.remote.app.AppModel
import ch.mixel.remote.app.AppPreferences
import ch.mixel.remote.app.Section
import ch.mixel.remote.app.VideoPacket
import ch.mixel.remote.engine.FakeEngine
import java.time.Instant
import kotlinx.coroutines.CoroutineScope

/**
 * Scripted engine states for the view-model tests and the screenshots of every screen. The real
 * app only shows them in debug builds (`--es demo <screen>`), never in a release build.
 */
object DemoFixtures {
    const val PROFILE = "11111111-2222-4333-8444-555555555555"
    const val TENANT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    const val SESSION = "0f0e0d0c-0b0a-4908-8706-050403020100"
    const val CONSENT = "c0c0c0c0-0000-4000-8000-000000000001"

    val screens = listOf("signed-out", "second-step", "devices", "connect", "setup", "account", "settings", "session", "chat", "files", "help")

    fun signedOut(engine: FakeEngine) {
        engine.reply("account_status", """{"phase":"signed_out","message":"Sign in with your Mixel account","account":null,"factors":[]}""")
        engine.reply("account_providers", """["keycloak","google","azure","apple"]""")
        engine.reply("remote_status", """{"phase":"offline","message":""}""")
    }

    fun mfa(engine: FakeEngine) {
        engine.reply(
            "account_status",
            """{"phase":"mfa_required","message":"Additional verification is required for this Mixel account","factors":[{"id":"f0000000-0000-4000-8000-000000000001","factor_type":"totp","status":"verified","friendly_name":"Mixel ISM staff"}]}""",
        )
        engine.reply("remote_status", """{"phase":"offline","message":""}""")
    }

    fun company(engine: FakeEngine, phase: String = "ready", role: String = "owner", staff: Boolean = false) {
        engine.reply(
            "account_status",
            """{"phase":"authenticated","message":"Signed in","account":{"remembered":true,"has_company":true,"has_personal":false,"profile":"$PROFILE","organization":"$TENANT","staff":$staff,"personal":false,"display_name":"Anna Muster","companies":[]},"factors":[]}""",
        )
        engine.reply("remote_status", """{"phase":"$phase","message":"","host":482913557,"online":[731204118,268455901]}""")
        engine.reply("saas_my_tenants", """[{"tenant_id":"$TENANT","organization_id":"$TENANT","name":"Muster Treuhand AG","slug":"muster","role":"$role","plan":"business","status":"trial","trial_ends_at":"2026-11-27T10:00:00Z"}]""")
        engine.reply(
            "saas_tenant_overview",
            """{"role":"$role","status":"trial","plan":"business","trial_ends_at":"2026-11-27T10:00:00Z","features":{"groups":true},"limits":{"devices":2000,"technicians":15},"usage":{"devices":4,"technicians":2}}""",
        )
        engine.reply(
            "saas_members",
            """[{"user_id":"$PROFILE","name":"Anna Muster","email":"anna@muster.test","role":"owner","created_at":"2026-11-01T08:00:00Z"},
               {"user_id":"22222222-2222-4222-8222-222222222222","name":"Marco Rossi","email":"marco@muster.test","role":"technician","created_at":"2026-11-02T08:00:00Z"}]""",
        )
        engine.reply("saas_invites", """[{"id":"33333333-3333-4333-8333-333333333333","email":"lea@muster.test","role":"technician","created_at":"2026-11-12T08:00:00Z","expires_at":"2026-11-19T08:00:00Z","mail_state":"delivered"}]""")
        engine.reply(
            "saas_devices",
            """[{"device_id":"d1000000-0000-4000-8000-000000000001","name":"Reception PC","os":"windows","group_id":"g1000000-0000-4000-8000-000000000001","group_name":"Front office","connection_id":731204118,"generation":3,"revoked":false},
               {"device_id":"d1000000-0000-4000-8000-000000000002","name":"Accounting Mac","os":"macos","connection_id":268455901,"generation":1,"revoked":false},
               {"device_id":"d1000000-0000-4000-8000-000000000003","name":"Warehouse laptop","os":"linux","connection_id":519003442,"generation":2,"revoked":false},
               {"device_id":"d1000000-0000-4000-8000-000000000004","name":"Anna’s Pixel","os":"linux","connection_id":482913557,"generation":1,"revoked":false}]""",
        )
        engine.reply("saas_groups", """[{"group_id":"g1000000-0000-4000-8000-000000000001","name":"Front office","devices":1,"technicians":[]}]""")
        val seen = Instant.now().minusSeconds(3 * 3600).toString()
        engine.reply("saas_device_presence", """[{"device_id":"d1000000-0000-4000-8000-000000000003","connection_id":519003442,"status":"offline","last_seen":"$seen"}]""")
        engine.reply("startup_settings", """{"remembered":true,"autostart":false,"launched_at_startup":false,"restored":true}""")
        engine.reply("device_removal_pending", "false")
        engine.reply("this_computer_name", "\"Mixel Remote (Pixel 8)\"")
        engine.reply("quick_phone_claim", "false")
    }

    fun viewing(engine: FakeEngine) {
        company(engine)
        engine.reply(
            "remote_status",
            """{"phase":"active","message":"","host":482913557,"session":"$SESSION","consent":"$CONSENT","peer":731204118,"received":12,"control":true,"clipboard":true,"files":true,
                "displays":2,"host_actions":["privacy","block","pointer","lock","lockend","sas","restart","sound","meet"],"host_macs":["525400a1b2c3"],"peer_features":["join","switch"],"online":[731204118]}""",
        )
        engine.reply("remote_chat_messages", """[{"id":1,"mine":false,"text":"Hello, the printer shows an error."},{"id":2,"mine":true,"text":"I see it, one moment."}]""")
        engine.reply(
            "remote_file_status",
            """{"connected":true,"incoming":{"id":"f2000000-0000-4000-8000-000000000002","name":"scan-0042.pdf","size":2400000,"offset":0,"phase":"offered","message":""},
                "outgoing":{"id":"f1000000-0000-4000-8000-000000000001","name":"driver-setup.exe","size":48000000,"offset":31000000,"phase":"transferring","message":""},"message":""}""",
        )
    }

    /** An app model with this engine state read once. */
    suspend fun app(engine: FakeEngine, scope: CoroutineScope, preferences: AppPreferences = AppPreferences.Memory()): AppModel {
        val app = AppModel(engine, scope, preferences, appCallback = "ch.mixel.remote://auth/callback")
        app.loadProviders()
        app.refreshAccount()
        app.company.load()
        app.refreshRemote()
        app.devices.load()
        app.settings.load()
        return app
    }

    /** A ready app model for one named screen (screenshots of the real app). */
    suspend fun screen(name: String, scope: CoroutineScope): AppModel {
        val engine = FakeEngine()
        when (name) {
            "signed-out" -> signedOut(engine)
            "second-step" -> mfa(engine)
            "setup" -> {
                company(engine, phase = "offline")
                engine.fail("remote_register", "No saved device identity. Choose Create device identity to generate one locally.")
            }
            "help" -> company(engine, staff = true)
            "session", "chat", "files" -> {
                viewing(engine)
                for (sequence in 1L..3L) engine.queue(VideoPacket.sample(1280, 800, sequence))
            }
            else -> company(engine)
        }
        val app = app(engine, scope)
        app.section = when (name) {
            "signed-out", "second-step", "account", "settings" -> Section.Account
            "connect", "setup", "help" -> Section.Connect
            else -> Section.Devices
        }
        if (name in setOf("session", "chat", "files")) app.session.readTools()
        return app
    }
}
