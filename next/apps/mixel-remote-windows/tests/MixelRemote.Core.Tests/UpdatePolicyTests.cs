using System.Text.Json;
using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Tests;

/// <summary>When the regular update check installs by itself, waits, retries a failed install, or asks the person.</summary>
[TestClass]
public sealed class UpdatePolicyTests
{
    private static readonly DateTimeOffset Now = new(2026, 11, 17, 12, 0, 0, TimeSpan.Zero);
    private static UpdateInfo Info(bool silent = true, bool keeps = true, bool waits = false, bool installable = true) =>
        new() { Version = "2.1.1", Installable = installable, Silent = silent, KeepsSignIn = keeps, WaitsForSession = waits };

    [TestMethod]
    public void NothingNewMeansNothingToDo()
    {
        Assert.AreEqual(UpdateAction.Nothing, UpdatePolicy.Decide(null, true, false, 0, null, Now));
        Assert.AreEqual(UpdateAction.Nothing, UpdatePolicy.Decide(Info(installable: false), true, false, 0, null, Now));
        Assert.AreEqual(UpdateAction.Nothing, UpdatePolicy.Decide(Info(), true, true, 0, null, Now), "already installing");
    }

    [TestMethod]
    public void AnIdleComputerThatKeepsTheSignInInstallsByItself()
    {
        Assert.AreEqual(UpdateAction.Install, UpdatePolicy.Decide(Info(), true, false, 0, null, Now));
    }

    [TestMethod]
    public void ASessionHoldsTheUpdateBack()
    {
        Assert.AreEqual(UpdateAction.WaitForSession, UpdatePolicy.Decide(Info(silent: false, waits: true), true, false, 0, null, Now));
        Assert.AreEqual(UpdateAction.WaitForSession, UpdatePolicy.Decide(Info(silent: false, keeps: false, waits: true), true, false, 0, null, Now), "the session wins over the prompt");
    }

    [TestMethod]
    public void SignedInWithoutStaySignedInGetsAPromptInsteadOfNeverUpdating()
    {
        Assert.AreEqual(UpdateAction.Prompt, UpdatePolicy.Decide(Info(silent: false, keeps: false), true, false, 0, null, Now));
        Assert.AreEqual(UpdateAction.Prompt, UpdatePolicy.Decide(Info(), false, false, 0, null, Now), "automatic updates off: the person is still told");
    }

    [TestMethod]
    public void NotYetIdleIsNotAPrompt()
    {
        Assert.AreEqual(UpdateAction.Nothing, UpdatePolicy.Decide(Info(silent: false, keeps: true), true, false, 0, null, Now));
    }

    [TestMethod]
    public void AFailedInstallIsRetriedLaterWithBackOff()
    {
        Assert.AreEqual(TimeSpan.FromMinutes(5), UpdatePolicy.BackOff(1));
        Assert.AreEqual(TimeSpan.FromMinutes(10), UpdatePolicy.BackOff(2));
        Assert.AreEqual(TimeSpan.FromMinutes(20), UpdatePolicy.BackOff(3));
        Assert.AreEqual(TimeSpan.FromHours(4), UpdatePolicy.BackOff(50), "never longer than four hours");
        Assert.AreEqual(UpdateAction.BackOff, UpdatePolicy.Decide(Info(), true, false, 1, Now.AddMinutes(-4), Now));
        Assert.AreEqual(UpdateAction.Install, UpdatePolicy.Decide(Info(), true, false, 1, Now.AddMinutes(-6), Now), "after the waiting time the next silent try happens");
        Assert.AreEqual(UpdateAction.BackOff, UpdatePolicy.Decide(Info(), true, false, 3, Now.AddMinutes(-19), Now));
        Assert.AreEqual(UpdateAction.Install, UpdatePolicy.Decide(Info(), true, false, 3, Now.AddMinutes(-21), Now));
    }

    [TestMethod]
    public void AFailedInstallDoesNotBlockLaterTriesForEver()
    {
        var failures = 1;
        var failedAt = Now;
        for (var tries = 0; tries < 8; tries++)
        {
            var later = failedAt + UpdatePolicy.BackOff(failures) + TimeSpan.FromSeconds(1);
            Assert.AreEqual(UpdateAction.Install, UpdatePolicy.Decide(Info(), true, false, failures, failedAt, later));
            failures++;
            failedAt = later;
        }
    }

    [TestMethod]
    public void AnAttemptThatDidNotFinishIsRememberedAcrossTheRestart()
    {
        var at = Now;
        var stored = UpdatePolicy.Encode("2.1.1", 2, at);
        Assert.AreEqual((2, (DateTimeOffset?)at), UpdatePolicy.Restore(stored, "2.1.0"), "the new version is not running: the installer failed");
        Assert.AreEqual((0, (DateTimeOffset?)null), UpdatePolicy.Restore(stored, "2.1.1"), "it is running: the update worked");
        Assert.AreEqual((0, (DateTimeOffset?)null), UpdatePolicy.Restore(stored, "2.2.0"));
        Assert.AreEqual((0, (DateTimeOffset?)null), UpdatePolicy.Restore(null, "2.1.0"));
        Assert.AreEqual((0, (DateTimeOffset?)null), UpdatePolicy.Restore("garbage", "2.1.0"));
    }

    [TestMethod]
    public void TheEngineAnswerCarriesWhetherTheSignInSurvives()
    {
        var lost = JsonSerializer.Deserialize<UpdateInfo>("""{"version":"2.1.1","installable":true,"silent":false,"keeps_sign_in":false}""", Json.Options)!;
        Assert.IsFalse(lost.KeepsSignIn);
        var older = JsonSerializer.Deserialize<UpdateInfo>("""{"version":"2.1.1","installable":true,"silent":true}""", Json.Options)!;
        Assert.IsTrue(older.KeepsSignIn, "an engine that does not say it keeps the sign-in");
    }
}
