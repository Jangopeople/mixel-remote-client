// C# 5 / .NET Framework 4. Primitive SD materialization only; no custody actors.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Web.Script.Serialization;

internal static class WindowsSdProbe
{
    private const string EpochSha256 = "b662ed35b2bea75c7f02214424186b983096565ab79bfbcb5625b5a165e6aeb7";
    private const string RootSd = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x001200a9;;;AU)(A;;0x00000002;;;AU)(A;;RC;;;OW)S:AI(ML;;NW;;;ME)";
    private const string MutexSd = "D:P(A;;0x001f0001;;;SY)(A;;0x001f0001;;;BA)(A;;0x00120000;;;AU)(A;;RC;;;OW)S:(ML;;NW;;;ME)";
    private const string MarkerSd = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x0013019f;;;{sid})(A;;RC;;;OW)S:AI(ML;;NW;;;ME)";
    private static readonly IntPtr InvalidHandle = new IntPtr(-1);
    private static readonly List<object> Checks = new List<object>();
    private static readonly List<object> Errors = new List<object>();
    private static readonly Dictionary<string, object> Observed = new Dictionary<string, object>();
    private static bool CleanupFailed;

    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityAttributes
    {
        public uint Length;
        public IntPtr Descriptor;
        public int Inherit;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct FileInfo
    {
        public uint Attributes, CreatedLow, CreatedHigh, AccessedLow, AccessedHigh;
        public uint WrittenLow, WrittenHigh, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    private sealed class NativeFailure : Exception
    {
        public readonly string Api;
        public readonly uint Code;
        public NativeFailure(string api, uint code) : base(new Win32Exception(unchecked((int)code)).Message)
        { Api = api; Code = code; }
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr LocalFree(IntPtr memory);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateDirectoryW(string path, ref SecurityAttributes attributes);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFileW(string path, uint access, uint share, IntPtr attributes,
        uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "CreateFileW")]
    private static extern IntPtr CreateMarker(string path, uint access, uint share, ref SecurityAttributes attributes,
        uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateMutexExW(ref SecurityAttributes attributes, string name, uint flags, uint access);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(IntPtr handle, out FileInfo information);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFileAttributesW(string path);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetFileInformationByHandle(IntPtr handle, uint informationClass, ref byte information, uint size);
    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError = true, EntryPoint = "GetTokenInformation")]
    private static extern bool TokenElevation(IntPtr token, uint informationClass, out uint value, uint size, out uint returned);
    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetTokenInformation(IntPtr token, uint informationClass, IntPtr value, uint size, out uint returned);
    [DllImport("advapi32.dll")]
    private static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
    [DllImport("advapi32.dll")]
    private static extern IntPtr GetSidSubAuthority(IntPtr sid, uint index);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text, uint revision,
        out IntPtr descriptor, IntPtr length);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool ConvertSecurityDescriptorToStringSecurityDescriptorW(IntPtr descriptor, uint revision,
        uint information, out IntPtr text, IntPtr length);
    [DllImport("advapi32.dll")]
    private static extern uint GetSecurityInfo(IntPtr handle, uint kind, uint information, IntPtr owner,
        IntPtr group, IntPtr dacl, IntPtr sacl, out IntPtr descriptor);
    [DllImport("advapi32.dll", EntryPoint = "GetSecurityInfo")]
    private static extern uint GetOwnerInfo(IntPtr handle, uint kind, uint information, out IntPtr owner,
        IntPtr group, IntPtr dacl, IntPtr sacl, out IntPtr descriptor);

    private static void Native(bool success, string api)
    { if (!success) throw new NativeFailure(api, unchecked((uint)Marshal.GetLastWin32Error())); }
    private static void Require(bool success, string message)
    { if (!success) throw new InvalidOperationException(message); }
    private static bool Valid(IntPtr handle) { return handle != IntPtr.Zero && handle != InvalidHandle; }
    private static void Free(ref IntPtr memory)
    {
        if (memory == IntPtr.Zero) return;
        IntPtr value = memory; memory = IntPtr.Zero;
        if (LocalFree(value) != IntPtr.Zero)
            Failure("cleanup:LocalFree", new NativeFailure("LocalFree", unchecked((uint)Marshal.GetLastWin32Error())));
    }
    private static void Close(ref IntPtr handle)
    {
        if (!Valid(handle)) return;
        IntPtr value = handle; handle = IntPtr.Zero;
        if (!CloseHandle(value))
            Failure("cleanup:CloseHandle", new NativeFailure("CloseHandle", unchecked((uint)Marshal.GetLastWin32Error())));
    }
    private static string Canonical(IntPtr descriptor)
    {
        IntPtr text = IntPtr.Zero;
        try
        {
            Native(ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4 | 16,
                out text, IntPtr.Zero), "ConvertSecurityDescriptorToStringSecurityDescriptorW");
            Require(text != IntPtr.Zero, "Canonical SDDL pointer absent");
            string value = Marshal.PtrToStringUni(text);
            Require(value != null && value.Length < 16384, "Canonical SDDL missing or too large");
            return value;
        }
        finally { Free(ref text); }
    }
    private static IntPtr Descriptor(string text)
    {
        IntPtr value;
        Native(ConvertStringSecurityDescriptorToSecurityDescriptorW(text, 1, out value, IntPtr.Zero),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW");
        Require(value != IntPtr.Zero, "SDDL descriptor pointer absent");
        return value;
    }
    private static string CanonicalExpected(string text)
    {
        IntPtr descriptor = Descriptor(text);
        try { return Canonical(descriptor); }
        finally { Free(ref descriptor); }
    }
    private static string ReadDescriptor(IntPtr handle, uint kind)
    {
        IntPtr descriptor = IntPtr.Zero;
        uint code = GetSecurityInfo(handle, kind, 4 | 16, IntPtr.Zero, IntPtr.Zero,
            IntPtr.Zero, IntPtr.Zero, out descriptor);
        // GetSecurityInfo returns its own DWORD error; do not replace it with last-error.
        if (code != 0) throw new NativeFailure("GetSecurityInfo(DACL|LABEL)", code);
        try { Require(descriptor != IntPtr.Zero, "Queried descriptor absent"); return Canonical(descriptor); }
        finally { Free(ref descriptor); }
    }
    private static void Check(string name, string actual, string expected, bool equal)
    {
        bool pass = String.Equals(actual, CanonicalExpected(expected), StringComparison.Ordinal) == equal;
        Checks.Add(new Dictionary<string, object> { { "name", name }, { "pass", pass } });
        Require(pass, "Exact descriptor assertion failed: " + name);
    }
    private static void Owned(IntPtr handle, string sid)
    {
        IntPtr owner, descriptor;
        uint code = GetOwnerInfo(handle, 1, 1, out owner, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, out descriptor);
        if (code != 0) throw new NativeFailure("GetSecurityInfo(OWNER)", code);
        try
        {
            Require(owner != IntPtr.Zero, "Owner SID absent");
            Require(new SecurityIdentifier(owner).Value == sid, "TEMP or fixture owner differs from process SID");
        }
        finally { Free(ref descriptor); }
    }
    private static FileInfo Ordinary(IntPtr handle, bool directory)
    {
        FileInfo information;
        Native(GetFileInformationByHandle(handle, out information), "GetFileInformationByHandle");
        Require((information.Attributes & 0x400) == 0, "Reparse fixture refused");
        Require(((information.Attributes & 0x10) != 0) == directory, "Fixture type differs");
        Require(information.IndexHigh != 0 || information.IndexLow != 0, "Fixture file identity absent");
        if (!directory) Require(information.Links == 1, "Hardlinked marker refused");
        return information;
    }
    private static void BindPath(string path, IntPtr retained, bool directory)
    {
        FileInfo original = Ordinary(retained, directory);
        IntPtr reopened = CreateFileW(path, 0x80, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
        Native(Valid(reopened), "CreateFileW(read-only fixture path identity)");
        try
        {
            FileInfo current = Ordinary(reopened, directory);
            Require(current.Volume == original.Volume && current.IndexHigh == original.IndexHigh &&
                current.IndexLow == original.IndexLow, "Retained fixture/path identity differs");
        }
        finally { Close(ref reopened); }
    }
    private static string ProcessSid(out IntPtr token)
    {
        Native(OpenProcessToken(GetCurrentProcess(), 0x8, out token), "OpenProcessToken(TOKEN_QUERY)");
        uint elevation, returned;
        Native(TokenElevation(token, 20, out elevation, 4, out returned), "GetTokenInformation(TokenElevation)");
        Require(returned == 4 && elevation == 0, "Run only with the existing non-elevated token; do not elevate");
        uint size;
        bool first = GetTokenInformation(token, 25, IntPtr.Zero, 0, out size);
        uint code = unchecked((uint)Marshal.GetLastWin32Error());
        if (first || code != 122) throw new NativeFailure("GetTokenInformation(TokenIntegrityLevel size)", code);
        Require(size >= IntPtr.Size + 4 && size <= 65536, "Integrity token size invalid");
        IntPtr buffer = Marshal.AllocHGlobal(checked((int)size));
        try
        {
            Native(GetTokenInformation(token, 25, buffer, size, out returned), "GetTokenInformation(TokenIntegrityLevel)");
            IntPtr integritySid = Marshal.ReadIntPtr(buffer);
            Require(integritySid != IntPtr.Zero, "Integrity SID absent");
            byte count = Marshal.ReadByte(GetSidSubAuthorityCount(integritySid));
            Require(count > 0, "Integrity RID absent");
            uint rid = unchecked((uint)Marshal.ReadInt32(GetSidSubAuthority(integritySid, (uint)(count - 1))));
            Require(rid == 0x2000, "Existing non-elevated medium-integrity token required; no privilege changes");
        }
        finally { Marshal.FreeHGlobal(buffer); }
        using (WindowsIdentity identity = new WindowsIdentity(token))
        { Require(identity.User != null, "Process user SID absent"); return identity.User.Value; }
    }
    private static string TempParent()
    {
        string path = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar);
        string root = Path.GetPathRoot(path);
        Require(root != null && root.Length == 3 && root[1] == ':', "Local drive TEMP required");
        DriveInfo drive = new DriveInfo(root);
        Require(drive.DriveType == DriveType.Fixed && drive.DriveFormat == "NTFS", "Fixed NTFS TEMP required");
        string current = root;
        foreach (string component in path.Substring(root.Length).Split(Path.DirectorySeparatorChar))
        {
            Require(component.Length > 0 && component != "." && component != "..", "Ordinary TEMP ancestry required");
            current = Path.Combine(current, component);
            Require((File.GetAttributes(current) & FileAttributes.ReparsePoint) == 0, "Linked TEMP ancestry refused");
        }
        return path;
    }
    private static void Failure(string stage, Exception error)
    {
        if (stage.StartsWith("cleanup", StringComparison.Ordinal)) CleanupFailed = true;
        NativeFailure native = error as NativeFailure;
        Errors.Add(new Dictionary<string, object> { { "stage", stage }, { "message", error.Message },
            { "api", native == null ? null : native.Api },
            { "nativeCode", native == null ? (object)null : native.Code } });
    }
    private static void DeleteFresh(ref IntPtr handle, string name)
    {
        if (!Valid(handle)) return;
        try
        {
            byte remove = 1;
            Native(SetFileInformationByHandle(handle, 4, ref remove, 1), "SetFileInformationByHandle(FileDispositionInfo:" + name + ")");
        }
        catch (Exception error) { Failure("cleanup:" + name, error); }
        try { Close(ref handle); }
        catch (Exception error) { Failure("cleanup:close:" + name, error); }
    }
    private static int Main(string[] args)
    {
        IntPtr token = IntPtr.Zero, parent = IntPtr.Zero, directory = IntPtr.Zero;
        IntPtr marker = IntPtr.Zero, mutex = IntPtr.Zero, descriptor = IntPtr.Zero;
        string fixture = null, temp = null;
        bool created = false, directoryOwned = false, markerOwned = false, removed = false;
        try
        {
            Require(args.Length == 2 && args[0] == "--epoch-source", "Usage: WindowsSdProbe.exe --epoch-source exact-epoch.rs");
            using (SHA256 hash = SHA256.Create())
            {
                string actual = BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(args[1]))).Replace("-", "").ToLowerInvariant();
                Require(actual == EpochSha256, "Exact Rust epoch source SHA256 mismatch; do not rebind or normalize");
            }
            Require(Environment.OSVersion.Platform == PlatformID.Win32NT && IntPtr.Size == 8, "Windows x64 process required");
            string sid = ProcessSid(out token);
            temp = TempParent();
            // Query and require the ordinary owned TEMP parent's delete-child access
            // before creating ROOT_SD, whose protected DACL gives the owner only RC.
            parent = CreateFileW(temp, 0x000200c4, 3, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            Native(Valid(parent), "CreateFileW(TEMP READ_CONTROL|READ_ATTRIBUTES|ADD_SUBDIRECTORY|DELETE_CHILD)");
            Ordinary(parent, true); Owned(parent, sid); BindPath(temp, parent, true);
            string nonce = Guid.NewGuid().ToString("N");
            fixture = Path.Combine(temp, "MixelSDProbe-" + nonce);
            descriptor = Descriptor(RootSd);
            SecurityAttributes attributes = new SecurityAttributes();
            attributes.Length = checked((uint)Marshal.SizeOf(typeof(SecurityAttributes)));
            attributes.Descriptor = descriptor; attributes.Inherit = 0;
            Native(CreateDirectoryW(fixture, ref attributes), "CreateDirectoryW(fresh namespace; existing refused)");
            created = true; Free(ref descriptor);
            // DELETE is granted through the owned TEMP parent, not by widening ROOT_SD.
            directory = CreateFileW(fixture, 0x00130081, 3, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            Native(Valid(directory), "CreateFileW(fresh namespace DELETE via TEMP parent)");
            Ordinary(directory, true); Owned(directory, sid); BindPath(fixture, directory, true);
            directoryOwned = true;
            string rootObserved = ReadDescriptor(directory, 1);
            Observed.Add("namespace", rootObserved);
            Check("namespace corrected AI", rootObserved, RootSd, true);
            Check("namespace old no-AI refuses", rootObserved, RootSd.Replace("S:AI(", "S:("), false);
            Check("namespace widened rights refuses", rootObserved, RootSd.Replace("0x00000002;;;AU", "0x00000006;;;AU"), false);
            Check("namespace changed label refuses", rootObserved, RootSd.Replace(";;;ME)", ";;;HI)"), false);
            Check("namespace unprotected DACL refuses", rootObserved, RootSd.Replace("D:P(", "D:("), false);

            descriptor = Descriptor(MutexSd); attributes.Descriptor = descriptor;
            mutex = CreateMutexExW(ref attributes, "Local\\MixelSDProbe-" + nonce, 0, 0x00120000);
            uint mutexError = unchecked((uint)Marshal.GetLastWin32Error());
            if (!Valid(mutex)) throw new NativeFailure("CreateMutexExW(fresh Local mutex)", mutexError);
            Require(mutexError != 183, "Existing Local mutex refused; no adoption");
            Free(ref descriptor);
            string mutexObserved = ReadDescriptor(mutex, 6);
            Observed.Add("mutex", mutexObserved);
            Check("mutex concrete masks", mutexObserved, MutexSd, true);
            Check("mutex generic masks differ", mutexObserved, MutexSd.Replace("(A;;0x001f0001;;;", "(A;;GA;;;"), false);
            Check("mutex widened rights refuses", mutexObserved, MutexSd.Replace("0x00120000;;;AU", "0x00120001;;;AU"), false);
            Check("mutex changed label refuses", mutexObserved, MutexSd.Replace(";;;ME)", ";;;HI)"), false);
            Check("mutex unprotected DACL refuses", mutexObserved, MutexSd.Replace("D:P(", "D:("), false);

            string markerSd = MarkerSd.Replace("{sid}", sid);
            descriptor = Descriptor(markerSd); attributes.Descriptor = descriptor;
            marker = CreateMarker(Path.Combine(fixture, "fresh-marker.dirty"), 0x0013019f, 1,
                ref attributes, 1, 0x00200080, IntPtr.Zero);
            Native(Valid(marker), "CreateFileW(CREATE_NEW fresh marker)");
            Free(ref descriptor);
            Ordinary(marker, false); Owned(marker, sid);
            markerOwned = true;
            string markerObserved = ReadDescriptor(marker, 1);
            Observed.Add("marker", markerObserved);
            Check("marker corrected AI", markerObserved, markerSd, true);
            Check("marker old no-AI refuses", markerObserved, markerSd.Replace("S:AI(", "S:("), false);
            Check("marker widened rights refuses", markerObserved, markerSd.Replace("0x0013019f", "0x0017019f"), false);
            Check("marker changed label refuses", markerObserved, markerSd.Replace(";;;ME)", ";;;HI)"), false);
            Check("marker unprotected DACL refuses", markerObserved, markerSd.Replace("D:P(", "D:("), false);
        }
        catch (Exception error) { Failure("probe", error); }
        finally
        {
            if (markerOwned) DeleteFresh(ref marker, "marker");
            else try { Close(ref marker); } catch (Exception error) { Failure("cleanup:unverified marker", error); }
            try { Close(ref mutex); } catch (Exception error) { Failure("cleanup:mutex", error); }
            if (directoryOwned)
            {
                try
                {
                    BindPath(fixture, directory, true);
                    Require(Directory.GetFileSystemEntries(fixture).Length == 0, "Unexpected fixture entry; no recursive deletion");
                    DeleteFresh(ref directory, "namespace");
                }
                catch (Exception error) { Failure("cleanup:namespace", error); }
            }
            try { Close(ref directory); } catch (Exception error) { Failure("cleanup:close namespace", error); }
            try { Free(ref descriptor); } catch (Exception error) { Failure("cleanup:descriptor", error); }
            if (created)
            {
                try { BindPath(temp, parent, true); }
                catch (Exception error) { Failure("cleanup:TEMP path identity", error); }
                uint attributes = GetFileAttributesW(fixture);
                uint code = unchecked((uint)Marshal.GetLastWin32Error());
                if (attributes == UInt32.MaxValue && (code == 2 || code == 3)) removed = true;
                else if (attributes == UInt32.MaxValue) Failure("cleanup:verify removal", new NativeFailure("GetFileAttributesW(fresh fixture)", code));
                else Failure("cleanup:namespace", new InvalidOperationException("Fresh fixture remains; no recursive/path repair: " + fixture));
            }
            try { Close(ref parent); } catch (Exception error) { Failure("cleanup:TEMP", error); }
            try { Close(ref token); } catch (Exception error) { Failure("cleanup:token", error); }
        }
        bool pass = Errors.Count == 0 && Checks.Count == 15;
        Dictionary<string, object> result = new Dictionary<string, object> {
            { "scope", "native SD materialization primitive only; not hosted custody, cross-SID isolation, input, SAS or app qualification" },
            { "epochSourceSHA256", EpochSha256 }, { "pass", pass }, { "checks", Checks },
            { "observedCanonicalDescriptors", Observed }, { "errors", Errors },
            { "cleanupVerified", created && removed && !CleanupFailed }
        };
        Console.WriteLine(new JavaScriptSerializer().Serialize(result));
        return pass ? 0 : 1;
    }
}
