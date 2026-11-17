using System.Text.Json.Serialization;
using MixelRemote.Engine;
using MixelRemote.Support;

namespace MixelRemote.ViewModels;

/// <summary>
/// One row of remote_admin_devices (docs/mixel-remote-device-admin-api.md, section 2.1): what the Devices list
/// needs for the "Unattended" and "Update" badges, the version, the note and the owner.
/// </summary>
public sealed record AdminDevice
{
    [JsonPropertyName("device_id")] public string DeviceId { get; init; } = "";
    [JsonPropertyName("connection_id")] public uint ConnectionId { get; init; }
    public string Name { get; init; } = "";
    public string Os { get; init; } = "";
    [JsonPropertyName("group_name")] public string? GroupName { get; init; }
    public string? Note { get; init; }
    public bool Favorite { get; init; }
    /// <summary>default, on or off.</summary>
    [JsonPropertyName("unattended_setting")] public string UnattendedSetting { get; init; } = "default";
    [JsonPropertyName("unattended_default")] public bool UnattendedDefault { get; init; }
    [JsonPropertyName("unattended_allowed")] public bool UnattendedAllowed { get; init; } = true;
    /// <summary>online, offline, unknown or not_enrolled.</summary>
    public string Status { get; init; } = "unknown";
    [JsonPropertyName("last_seen")] public string? LastSeen { get; init; }
    [JsonPropertyName("app_version")] public string? AppVersion { get; init; }
    [JsonPropertyName("owner_name")] public string? OwnerName { get; init; }
    [JsonPropertyName("updated_at")] public string? UpdatedAt { get; init; }
    [JsonPropertyName("can_admin")] public bool CanAdmin { get; init; }

    /// <summary>The device accepts unattended connections: not switched off, and on by its own setting or the company default.</summary>
    public bool Unattended => UnattendedAllowed && (UnattendedSetting == "on" || (UnattendedSetting == "default" && UnattendedDefault));
}

/// <summary>
/// The device administration the Devices list talks to. One interface, so the app needs nothing but wiring
/// when the engine has the commands (the engine today has none of the remote_admin_* names; every call then
/// fails and the list simply shows no badges).
/// </summary>
public interface IDeviceAdmin
{
    Task<IReadOnlyList<AdminDevice>> Devices(string? company, string? search);
    Task<AdminDevice> Rename(AdminDevice device, string name);
    Task<AdminDevice> Move(AdminDevice device, string? groupId, string? groupName);
    Task<AdminDevice> SetNote(AdminDevice device, string? note);
    Task SetUnattended(AdminDevice device, string setting);
    Task Remove(AdminDevice device, uint confirmConnectionId);
}

/// <summary>The contract over the engine's remote_admin_* commands (same names and arguments as the RPC bodies).</summary>
public sealed class EngineDeviceAdmin(IEngine engine) : IDeviceAdmin
{
    public async Task<IReadOnlyList<AdminDevice>> Devices(string? company, string? search) =>
        await engine.RunAsync<List<AdminDevice>?>("remote_admin_devices", ("company", company), ("search", string.IsNullOrEmpty(search) ? null : search)) ?? [];

    public Task<AdminDevice> Rename(AdminDevice device, string name) =>
        engine.RunAsync<AdminDevice>("remote_admin_rename_device", ("device", device.DeviceId), ("device_name", name), ("expected_update", device.UpdatedAt));

    public Task<AdminDevice> Move(AdminDevice device, string? groupId, string? groupName) =>
        engine.RunAsync<AdminDevice>("remote_admin_move_device", ("device", device.DeviceId), ("group_id", groupId), ("group_name", groupName));

    public Task<AdminDevice> SetNote(AdminDevice device, string? note) =>
        engine.RunAsync<AdminDevice>("remote_admin_set_device_note", ("device", device.DeviceId), ("note", string.IsNullOrWhiteSpace(note) ? null : note.Trim()));

    public Task SetUnattended(AdminDevice device, string setting) =>
        engine.PerformAsync("remote_admin_set_device_unattended", ("device", device.DeviceId), ("setting", setting));

    public Task Remove(AdminDevice device, uint confirmConnectionId) =>
        engine.PerformAsync("remote_admin_remove_device", ("device", device.DeviceId), ("confirm_connection_id", confirmConnectionId));
}
