using System.Text;
using MixelRemote.Models;
using MixelRemote.Support;

namespace MixelRemote.Tests;

[TestClass]
public sealed class SupportTests
{
    [TestInitialize]
    public void English() => Translator.Shared.Choice = "en";

    [TestMethod]
    public void Remote_ids_are_grouped_and_parsed_strictly()
    {
        Assert.AreEqual("482 913 557", Format.RemoteId(482_913_557));
        Assert.AreEqual("— — —", Format.RemoteId(null));
        Assert.AreEqual(482_913_557u, Format.ParseRemoteId(" 482 913 557 "));
        Assert.IsNull(Format.ParseRemoteId("082913557"));
        Assert.IsNull(Format.ParseRemoteId("48291355"));
        Assert.IsNull(Format.ParseRemoteId("48291355x"));
    }

    [TestMethod]
    public void Video_frames_are_checked_like_the_desktop_app()
    {
        var sample = VideoFrame.Sample(64, 40, 5);
        var frame = VideoFrame.Decode(sample);
        Assert.AreEqual((64, 40, 5ul), (frame.Width, frame.Height, frame.Sequence));
        Assert.ThrowsExactly<VideoFrame.InvalidFrame>(() => VideoFrame.Decode(sample, after: 5), "old pictures are refused");
        Assert.ThrowsExactly<VideoFrame.InvalidFrame>(() => VideoFrame.Decode(sample[..^4]));
        var huge = VideoFrame.Sample(64, 40, 1);
        BitConverter.GetBytes(4000u).CopyTo(huge, 4);
        Assert.ThrowsExactly<VideoFrame.InvalidFrame>(() => VideoFrame.Decode(huge));
        Assert.IsTrue(VideoFrame.IsCompressed("MRH1...."u8.ToArray()));
        var bgra = frame.ToBgra();
        var rgba = frame.ToRgba();
        Assert.AreEqual(64 * 40 * 4, bgra.Length);
        Assert.AreEqual(rgba[0], bgra[2]);
        Assert.AreEqual(rgba[2], bgra[0]);
        Assert.AreEqual(255, bgra[3]);
    }

    [TestMethod]
    public void Windows_scan_codes_become_hid_usages()
    {
        Assert.AreEqual((ushort)4, KeyMap.Usage(0x1E, false)); // A
        Assert.AreEqual((ushort)29, KeyMap.Usage(0x2C, false)); // Z position (Y on a Swiss keyboard)
        Assert.AreEqual((ushort)40, KeyMap.Usage(0x1C, false)); // Enter
        Assert.AreEqual((ushort)88, KeyMap.Usage(0x1C, true)); // keypad Enter
        Assert.AreEqual((ushort)76, KeyMap.Usage(0x53, true)); // Delete
        Assert.AreEqual((ushort)99, KeyMap.Usage(0x53, false)); // keypad .
        Assert.AreEqual((ushort)227, KeyMap.Usage(0x5B, true)); // Windows key
        Assert.AreEqual((ushort)230, KeyMap.Usage(0x38, true)); // AltGr
        Assert.AreEqual((ushort)83, KeyMap.Usage(0x45, true)); // NumLock
        Assert.AreEqual((ushort)72, KeyMap.Usage(0x45, false)); // Pause
        Assert.AreEqual((ushort)100, KeyMap.Usage(0x56, false)); // < > on ISO keyboards
        Assert.IsNull(KeyMap.Usage(0x00, false));
    }

    [TestMethod]
    public void Input_events_are_encoded_as_the_desktop_app_sends_them()
    {
        Assert.AreEqual("""{"kind":"button_click","button":3,"down":true,"clicks":2}""", new InputEvent.ButtonClick(3, true, 2).ToJson());
        Assert.AreEqual("""{"kind":"wheel","horizontal":0,"vertical":-1}""", new InputEvent.Wheel(0, -1).ToJson());
        Assert.AreEqual("""{"kind":"release_all"}""", new InputEvent.ReleaseAll().ToJson());
        Assert.AreEqual("""{"kind":"select_display","index":1}""", new InputEvent.SelectDisplay(1).ToJson());
        var text = System.Text.Json.Nodes.JsonNode.Parse(new InputEvent.Text("Grüezi \"du\"").ToJson())!;
        Assert.AreEqual("text", text["kind"]!.GetValue<string>());
        Assert.AreEqual("Grüezi \"du\"", text["text"]!.GetValue<string>());
    }

    [TestMethod]
    public void Every_screen_text_has_german_french_and_italian()
    {
        // texts.json is generated from the code by scripts/make-texts.py, which fails on a missing text;
        // this checks the shipped file has all four languages for a sample of keys.
        foreach (var key in new[] { "Devices", "Remote control", "Settings", "Account", "End session", "Ctrl+Alt+Del", "Allow view only" })
        {
            foreach (var language in new[] { "de", "fr", "it" }) Assert.IsTrue(Translator.Shared.Knows(key, language), $"{language}: {key}");
        }
        Assert.AreEqual("Geräte", Translator.Shared.Translate("Devices", "de"));
        Assert.AreEqual("Page 2 · 7 devices.", T.L("Page %d · %d devices.", 2, 7));
        Assert.AreEqual("1,234", Translator.Fill("%d", [1234], System.Globalization.CultureInfo.GetCultureInfo("en-GB")));
    }

    [TestMethod]
    public void Engine_messages_are_translated_with_the_desktop_tables()
    {
        Assert.AreNotEqual("Go offline before adding this computer", Translator.Shared.Translate("Go offline before adding this computer", "de"));
        Assert.AreEqual("Some unknown text 42", Translator.Shared.Translate("Some unknown text 42", "fr"));
        Assert.AreEqual("Anything", Translator.Shared.Translate("Anything", "en"));
    }

    [TestMethod]
    public void Last_seen_reads_like_a_person_would_say_it()
    {
        var now = DateTimeOffset.Parse("2026-11-13T12:00:00Z", System.Globalization.CultureInfo.InvariantCulture);
        Assert.AreEqual("Last seen just now", Format.LastSeen("2026-11-13T11:59:30Z", now));
        Assert.AreEqual("Last seen 5 minutes ago", Format.LastSeen("2026-11-13T11:55:00Z", now));
        Assert.AreEqual("Last seen 1 hour ago", Format.LastSeen("2026-11-13T11:00:00Z", now));
        Assert.AreEqual("Last seen yesterday", Format.LastSeen("2026-11-12T12:00:00Z", now));
        Assert.AreEqual("", Format.LastSeen("not a date", now));
    }

    private static byte[] Record(string origin, string key, string value, bool utf16 = false)
    {
        var full = Encoding.Latin1.GetBytes("_" + origin + "\0\u0001" + key);
        var encoded = utf16 ? [0, .. Encoding.Unicode.GetBytes(value)] : (byte[])[1, .. Encoding.Latin1.GetBytes(value)];
        return [1, (byte)full.Length, .. full, (byte)encoded.Length, .. encoded];
    }

    [TestMethod]
    public void Settings_of_the_0_1_app_are_taken_over_once()
    {
        var folder = Path.Combine(Path.GetTempPath(), "mixel-legacy-" + Guid.NewGuid());
        Directory.CreateDirectory(folder);
        try
        {
            byte[] older = [.. Record("tauri://localhost", "mixel.remote.language.v1", "fr")];
            byte[] newer =
            [
                0x10, 0x20, .. Record("http://tauri.localhost", "mixel.remote.language.v1", "de"),
                .. Record("http://tauri.localhost", "mixel.remote.unattended.v1", """{"source":"dxgi:0:0:\\\\.\\DISPLAY1","control":true}"""),
                .. Record("http://tauri.localhost", "mixel.remote.this-computer.v1", """{"device":"d1","connection_id":482913557,"generation":3,"name":"PC","tenant":"t1"}""", utf16: true),
                .. Record("http://tauri.localhost", "mixel.remote.macs.v1", """{"731204118":["00:11:22:33:44:55"]}"""),
                .. Record("http://tauri.localhost", "mixel.remote.autoupdate.v1", "off"),
                0xFF,
            ];
            File.WriteAllBytes(Path.Combine(folder, "000003.log"), older);
            File.SetLastWriteTimeUtc(Path.Combine(folder, "000003.log"), DateTime.UtcNow.AddDays(-2));
            File.WriteAllBytes(Path.Combine(folder, "000005.log"), newer);
            var preferences = new MemoryPreferences();
            preferences.Set("mixel.remote.autoupdate.v1", "on");
            var taken = LegacySettings.Import(folder, preferences);
            Assert.AreEqual("de", preferences.Get("mixel.remote.language.v1"), "the newest record wins");
            StringAssert.Contains(preferences.Get("mixel.remote.unattended.v1"), "\"control\":true");
            StringAssert.Contains(preferences.Get("mixel.remote.this-computer.v1"), "482913557");
            StringAssert.Contains(preferences.Get("mixel.remote.macs.v1"), "00:11:22:33:44:55");
            Assert.AreEqual("on", preferences.Get("mixel.remote.autoupdate.v1"), "a value set in 2.0 is never overwritten");
            Assert.AreEqual(4, taken.Count);
            preferences.Set("mixel.remote.language.v1", null);
            Assert.AreEqual(0, LegacySettings.Import(folder, preferences).Count, "only once");
            var registration = System.Text.Json.JsonSerializer.Deserialize<Registration>(preferences.Get("mixel.remote.this-computer.v1")!, Engine.Json.Options);
            Assert.AreEqual(3ul, registration?.Generation);
        }
        finally
        {
            Directory.Delete(folder, recursive: true);
        }
    }

    [TestMethod]
    public void A_missing_folder_imports_nothing()
    {
        var preferences = new MemoryPreferences();
        Assert.AreEqual(0, LegacySettings.Import(Path.Combine(Path.GetTempPath(), "does-not-exist-" + Guid.NewGuid()), preferences).Count);
        Assert.AreEqual("1", preferences.Get(LegacySettings.DoneKey));
    }
}
