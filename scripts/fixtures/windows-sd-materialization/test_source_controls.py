#!/usr/bin/env python3
"""Source controls only. Does not compile C# or invoke any Windows API."""
import hashlib
import json
from pathlib import Path
import re
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
CS = (HERE / 'WindowsSdProbe.cs').read_text()
EPOCH = (ROOT / 'scripts/support-receiver-custodian-epoch-windows.rs').read_bytes()
MANIFEST = json.loads((HERE / 'source-manifest.json').read_text())


class SourceControls(unittest.TestCase):
    def test_complete_current_rust_epoch_source_is_exactly_bound_and_copied(self):
        digest = hashlib.sha256(EPOCH).hexdigest()
        self.assertEqual(digest, 'f362530835fd3a45cb98d8616dc7517cc077189d700a1790e7700a434085c9a2')
        self.assertIn('private const string EpochSha256 = "' + digest + '";', CS)
        self.assertEqual((HERE / 'support-receiver-custodian-epoch-windows.rs').read_bytes(), EPOCH)
        self.assertLess(CS.index('hash.ComputeHash(File.ReadAllBytes(args[1]))'), CS.index('string sid = ProcessSid(out token)'))

    def test_all_production_descriptors_are_exact_with_only_runtime_process_sid(self):
        rust = EPOCH.decode()
        for rust_name, cs_name in (('ROOT_SD', 'RootSd'), ('MUTEX_SD', 'MutexSd')):
            expected = re.search(r'const ' + rust_name + r':&str="([^"]+)";', rust).group(1)
            actual = re.search(r'private const string ' + cs_name + r' = "([^"]+)";', CS).group(1)
            self.assertEqual(actual, expected)
        marker = re.search(r'let marker_sd=format!\("([^"]+)"\)', rust).group(1)
        self.assertIn('private const string MarkerSd = "' + marker + '";', CS)
        self.assertIn('OpenProcessToken(GetCurrentProcess(), 0x8, out token)', CS)
        self.assertIn('rid == 0x2000', CS)
        self.assertIn('elevation == 0', CS)

    def test_canonical_api_contract_and_native_error_channel_are_conserved(self):
        self.assertIn('ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4 | 16,', CS)
        query = CS[CS.index('private static string ReadDescriptor'):CS.index('private static void Check')]
        self.assertIn('GetSecurityInfo(handle, kind, 4 | 16, IntPtr.Zero, IntPtr.Zero,', query)
        self.assertIn('new NativeFailure("GetSecurityInfo(DACL|LABEL)", code)', query)
        self.assertNotIn('GetLastWin32Error', query)
        self.assertIn('String.Equals(actual, CanonicalExpected(expected), StringComparison.Ordinal)', CS)
        self.assertIn('finally { Free(ref text); }', CS)
        self.assertIn('"nativeCode", native == null ? (object)null : native.Code', CS)

    def test_freshness_identity_and_handle_only_cleanup_are_explicit(self):
        for required in ('Guid.NewGuid().ToString("N")', '"MixelSDProbe-" + nonce',
                         '"Local\\\\MixelSDProbe-" + nonce', 'mutexError != 183',
                         'CreateDirectoryW(fixture, ref attributes)', '0x000200c4, 3',
                         '0x00130081, 3', '0x0013019f, 1', 'ref attributes, 1, 0x00200080',
                         'current.Volume == original.Volume', 'current.IndexLow == original.IndexLow',
                         'information.Links == 1', 'DriveType.Fixed && drive.DriveFormat == "NTFS"',
                         'new SecurityIdentifier(owner).Value == sid',
                         'Directory.GetFileSystemEntries(fixture).Length == 0',
                         'SetFileInformationByHandle(handle, 4, ref remove, 1)',
                         'byte remove = 1', 'code == 2 || code == 3',
                         'created && removed && !CleanupFailed'):
            self.assertIn(required, CS)
        for forbidden in ('SetSecurityInfo(', 'SetNamedSecurityInfo', 'AdjustTokenPrivileges',
                          'SetAccessControl', 'Directory.Delete', 'File.Delete', 'RemoveDirectory',
                          'DeleteFile', 'Process.Start', 'CreateProcess', 'SendInput', 'SendSAS',
                          'ProgramData', 'Mixel-Remote-Input-Custody-v1'):
            self.assertNotIn(forbidden, CS)

    def test_all_sixteen_positive_and_negative_controls_are_present(self):
        calls = re.findall(r'^            Check\("([^"]+)"', CS, re.M)
        self.assertEqual(len(calls), 16)
        self.assertEqual(sum('corrected AI' in value or value == 'mutex concrete masks' for value in calls), 3)
        self.assertEqual(sum('old no-AI' in value for value in calls), 3)
        self.assertEqual(sum('widened rights' in value for value in calls), 3)
        self.assertEqual(sum('changed label' in value for value in calls), 3)
        self.assertEqual(sum('unprotected DACL' in value for value in calls), 3)
        self.assertIn('mutex generic masks differ', calls)
        for changed in ('0x00000006;;;AU', '0x00120001;;;AU', '0x0017019f', ';;;HI)', '(A;;GA;;;'):
            self.assertIn(changed, CS)

    def test_exact_artifact_manifest_and_framework_only_commands(self):
        for name, expected in MANIFEST['files'].items():
            self.assertEqual(hashlib.sha256((HERE / name).read_bytes()).hexdigest(), expected, name)
        commands = (HERE / 'COMMANDS.txt').read_text()
        for required in ('Framework64\\v4.0.30319\\csc.exe', '/langversion:5', '/platform:x64',
                         '/warnaserror+', '/reference:System.Web.Extensions.dll', '--epoch-source $Epoch'):
            self.assertIn(required, commands)
        for modern in ('record ', ' init;', '=>', '$"', 'using var ', 'nameof(', 'ValueTuple', 'System.Text.Json'):
            self.assertNotIn(modern, CS)
        self.assertIn('not hosted custody, cross-SID isolation, input, SAS or app qualification', CS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
