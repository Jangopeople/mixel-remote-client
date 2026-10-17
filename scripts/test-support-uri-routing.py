#!/usr/bin/env python3
"""Exact full URI routing functions, strict source ownership and inverse controls."""
import argparse

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path
# Load the exact sibling helper for direct, runpy and file-spec entry points.
_input_lifecycle_spec = importlib.util.spec_from_file_location(
    "mixel_input_lifecycle_projection",
    Path(__file__).with_name("input_lifecycle_integration_projection.py"),
)
input_lifecycle = importlib.util.module_from_spec(_input_lifecycle_spec)
_input_lifecycle_spec.loader.exec_module(input_lifecycle)

BASE = Path(__file__).absolute().parent
spec = importlib.util.spec_from_file_location('uri_routing_owner', BASE / 'patch-support-uri-routing.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
SOURCE = None
GIT_SOURCE = None
OUTPUT = None
DART = None

DART_PRELUDE = r"""
import 'dart:async';
import 'dart:convert';
import 'mixel_support_invite.dart';
bool isLinux=false,isWeb=false,isWindows=false,isMacOS=true;
const isMobile=false,isAndroid=false,isIOS=false;
String webInitialLink='';
String? initialLink;
Future<String?> getInitialLink() async=>initialLink;
void registerProtocol(String protocol){}
void debugPrint(String message){}
final links=StreamController<String?>.broadcast();
Stream<String?> get linkStream=>links.stream;
final outbound=<String>[];
final reports=<String>[];
final reportKeys=<String>[];
var shown=0;
void windowOnTop(int? id){shown++;}
Timer? _supportInviteAttendedTimer;
Future<bool> _renewSupportInviteAttended() async=>true;
Future<void> _reportSupportInvite(String token,String apiKey)async{reports.add(token);reportKeys.add(apiKey);}
void setEnvTerminalAdmin(){}
class FakeBind{
 String mainUriPrefixSync()=>'mixel-remote://';
 Future<void> mainSetPermanentPassword({required String password})async{}
 void sendUrlScheme({required String url}){throw StateError('Unexpected native reroute');}
}
final bind=FakeBind();
class FakeManager{
 void newRemoteDesktop(String id,{String? password,String? switchUuid,bool? forceRelay}){outbound.add(id);}
 void newFileTransfer(String id,{String? password,bool? forceRelay}){outbound.add(id);}
 void newViewCamera(String id,{String? password,bool? forceRelay}){outbound.add(id);}
 void newPortForward(String id,bool rdp,{String? password,bool? forceRelay}){outbound.add(id);}
 void newTerminal(String id,{String? password,bool? forceRelay}){outbound.add(id);}
 Future<void> closeAllSubWindows()async{}
}
final rustDeskWinManager=FakeManager();
class Get{static Object? context;}
void connect(Object context,String id,{bool? isFileTransfer,bool? forceRelay,String? password,bool? isViewCamera,bool? isTerminal}){outbound.add(id);}
void importConfig(Object? a,Object? b,String config){}
String translate(String s)=>s;
void showToast(String s){}
const kUrlActionClose='close';
class FakeWindowManager{void close(){}}
final windowManager=FakeWindowManager();
"""
DART_TESTS = r"""
const token='inv_00000000-0000-0000-0000-000000000001';
const apiKey='synthetic-public-key-only-000000000000';
const base='mixel-remote://support?invite=$token&apikey=$apiKey';
class Case{
 final String name,url,want;
 const Case(this.name,this.url,this.want);
}
const cases=<Case>[
 Case('valid_product',base,'report'),
 Case('valid_product_slash','mixel-remote://support/?invite=$token&apikey=$apiKey','report'),
 Case('proper_percent_encoding','mixel-remote://support?invite=$token&apikey=synthetic%26encoded-key-000000000000','report'),
 Case('literal_html_ampersand','mixel-remote://support?invite=$token&amp;apikey=$apiKey','refuse'),
 Case('wrong_rustdesk_scheme','rustdesk://support?invite=$token&apikey=$apiKey','legacy-support'),
 Case('wrong_https_scheme','https://support?invite=$token&apikey=$apiKey','legacy-support'),
 Case('missing_invite','mixel-remote://support?apikey=$apiKey','refuse'),
 Case('missing_apikey','mixel-remote://support?invite=$token','refuse'),
 Case('invalid_invite','mixel-remote://support?invite=invalid&apikey=$apiKey','refuse'),
 Case('support_path_rejected','mixel-remote://support/r?invite=$token&apikey=$apiKey','refuse'),
 Case('support_userinfo_rejected','mixel-remote://user@support?invite=$token&apikey=$apiKey','refuse'),
 Case('support_port_rejected','mixel-remote://support:123?invite=$token&apikey=$apiKey','refuse'),
 Case('support_fragment_rejected','mixel-remote://support?invite=$token&apikey=$apiKey#foo','refuse'),
 Case('mixed_case_product','MiXeL-ReMoTe://SuPpOrT?invite=$token&apikey=$apiKey','report'),
 Case('ordinary_remote_id','mixel-remote://123456789','remote-id'),
 Case('ordinary_connect','mixel-remote://connect/123456789','remote-id'),
 Case('ordinary_compat','mixel-remote://connection/new/123456789','remote-id'),
 Case('ordinary_other_scheme_id','rustdesk://123456789','remote-id'),
];
void check(bool value,String message){if(!value)throw StateError(message);}
String classify(){
 if(reports.isNotEmpty){check(outbound.isEmpty,'Both support and outbound');return 'report';}
 if(outbound.isNotEmpty){check(outbound.length==1,'Duplicate outbound');return outbound.single=='support'?'legacy-support':'remote-id';}
 return 'refuse';
}
void reset(){outbound.clear();reports.clear();reportKeys.clear();shown=0;}
Future<void> flush()async{
 // Deliver queued broadcast events before the report callback's timer barrier.
 await Future<void>.microtask((){});
 await Future<void>.delayed(Duration.zero);
}
const secondToken='inv_00000000-0000-0000-0000-000000000002';
const secondKey='synthetic-second-public-key-000000000000';
class QueryCase{
 final String name,url;
 final String? expectedToken,expectedKey;
 const QueryCase(this.name,this.url,this.expectedToken,this.expectedKey);
}
const queryCases=<QueryCase>[
 QueryCase('duplicate_invite_two_valid','mixel-remote://support?invite=$token&invite=$secondToken&apikey=$apiKey',secondToken,apiKey),
 QueryCase('duplicate_invite_final_invalid','mixel-remote://support?invite=$token&invite=invalid&apikey=$apiKey',null,null),
 QueryCase('duplicate_invite_final_valid','mixel-remote://support?invite=invalid&invite=$token&apikey=$apiKey',token,apiKey),
 QueryCase('duplicate_apikey_two_valid','mixel-remote://support?invite=$token&apikey=$apiKey&apikey=$secondKey',token,secondKey),
 QueryCase('duplicate_apikey_final_invalid','mixel-remote://support?invite=$token&apikey=$apiKey&apikey=invalid',null,null),
 QueryCase('duplicate_apikey_final_valid','mixel-remote://support?invite=$token&apikey=invalid&apikey=$apiKey',token,apiKey),
 QueryCase('mixed_case_invite_two_valid','mixel-remote://support?invite=$token&INVITE=$secondToken&apikey=$apiKey',secondToken,apiKey),
 QueryCase('mixed_case_invite_later_inserted_invalid','mixel-remote://support?invite=$token&INVITE=invalid&invite=$token&apikey=$apiKey',null,null),
 QueryCase('mixed_case_invite_later_inserted_valid','mixel-remote://support?INVITE=invalid&invite=$token&INVITE=invalid&apikey=$apiKey',token,apiKey),
 QueryCase('mixed_case_apikey_two_valid','mixel-remote://support?invite=$token&apikey=$apiKey&APIKEY=$secondKey',token,secondKey),
 QueryCase('mixed_case_apikey_later_inserted_invalid','mixel-remote://support?invite=$token&apikey=$apiKey&APIKEY=invalid&apikey=$apiKey',null,null),
 QueryCase('mixed_case_apikey_later_inserted_valid','mixel-remote://support?invite=$token&APIKEY=invalid&apikey=$apiKey&APIKEY=invalid',token,apiKey),
 QueryCase('encoded_invite_alias_two_valid','mixel-remote://support?invite=$token&%69nvite=$secondToken&apikey=$apiKey',secondToken,apiKey),
 QueryCase('encoded_invite_alias_final_invalid','mixel-remote://support?invite=$token&%69nvite=invalid&apikey=$apiKey',null,null),
 QueryCase('encoded_invite_alias_final_valid','mixel-remote://support?%69nvite=invalid&invite=$token&apikey=$apiKey',token,apiKey),
 QueryCase('encoded_apikey_alias_two_valid','mixel-remote://support?invite=$token&apikey=$apiKey&ap%69key=$secondKey',token,secondKey),
 QueryCase('encoded_apikey_alias_final_invalid','mixel-remote://support?invite=$token&apikey=$apiKey&ap%69key=invalid',null,null),
 QueryCase('encoded_apikey_alias_final_valid','mixel-remote://support?invite=$token&ap%69key=invalid&apikey=$apiKey',token,apiKey),
 QueryCase('encoded_upper_alias_later_inserted_valid','mixel-remote://support?invite=$token&%41PIKEY=invalid&apikey=$apiKey&APIKEY=invalid',token,apiKey),
 QueryCase('encoded_upper_alias_later_inserted_invalid','mixel-remote://support?invite=$token&apikey=$apiKey&%41PIKEY=invalid',null,null),
 QueryCase('encoded_upper_invite_later_inserted_valid','mixel-remote://support?%49NVITE=invalid&invite=$token&INVITE=invalid&apikey=$apiKey',token,apiKey),
 QueryCase('identical_repeated_keys_and_values','mixel-remote://support?invite=$token&invite=$token&apikey=$apiKey&apikey=$apiKey',token,apiKey),
 QueryCase('both_parameters_final_valid_values','mixel-remote://support?invite=$token&apikey=$apiKey&invite=$secondToken&apikey=$secondKey',secondToken,secondKey),
 QueryCase('unknown_duplicate_aliases_preserve_support','mixel-remote://support?invite=$token&apikey=$apiKey&other=invalid&%6fther=anything',token,apiKey),
];
Future<void> main(List<String> flags)async{
 final platform=flags.isEmpty?'macos':flags.single;
 isLinux=platform=='linux';isWindows=platform=='windows';isMacOS=platform=='macos';
 check(isLinux||isWindows||isMacOS,'Invalid fixture platform');
 final sub=listenUniLinks();
 check((sub==null)==isLinux,'Actual platform listener guard differs');
 var assertions=0;
 final rows=<Map<String,String>>[];
 for(final c in cases){
   final parsed=urlLinkToCmdArgs(Uri.parse(c.url));
   final parserClass=parsed==null?'refuse':parsed.first=='--support-invite'?'report':parsed.length>1&&parsed[1]=='support'?'legacy-support':'remote-id';
   check(parserClass==c.want,'Parser case ${c.name}: $parserClass != ${c.want}'); assertions++;
   for(final path in ['direct','cold_plugin','warm_plugin','rust_event','boot_args']){
     reset();
     switch(path){
       case 'direct':handleUriLink(uriString:c.url);break;
       case 'cold_plugin':initialLink=c.url;await initUniLinks();break;
       case 'warm_plugin':links.add(c.url);break;
       case 'rust_event':onUrlSchemeReceived({'url':c.url});break;
       case 'boot_args':handleUriLink(cmdArgs:[c.url]);break;
     }
     await flush();
     final actual=classify();
     final want=isLinux&&(path=='cold_plugin'||path=='warm_plugin')||path=='rust_event'&&!c.url.startsWith(bind.mainUriPrefixSync())||path=='boot_args'&&!c.url.toLowerCase().startsWith(bind.mainUriPrefixSync())?'refuse':c.want;
     check(actual==want,'${c.name}/$path: $actual != $want');assertions++;
     rows.add({'case':c.name,'path':path,'result':actual});
   }
 }
 var queryParityAssertions=0;
 final queryRows=<Map<String,dynamic>>[];
 for(final c in queryCases){
   final parsed=urlLinkToCmdArgs(Uri.parse(c.url));
   final valid=c.expectedToken!=null;
   check(valid?(parsed!=null&&parsed.length==4&&parsed[0]=='--support-invite'&&
       parsed[1]==c.expectedToken&&parsed[2]=='--support-apikey'&&parsed[3]==c.expectedKey):parsed==null,
       'Duplicate query parser mapping differs: ${c.name}');
   queryParityAssertions++;
   for(final path in ['direct','cold_plugin','warm_plugin','rust_event','boot_args']){
     reset();bool? outboundIntent;
     switch(path){
       case 'direct':outboundIntent=handleUriLink(uriString:c.url);break;
       case 'cold_plugin':initialLink=c.url;outboundIntent=await initUniLinks();break;
       case 'warm_plugin':links.add(c.url);break;
       case 'rust_event':onUrlSchemeReceived({'url':c.url});break;
       case 'boot_args':outboundIntent=handleUriLink(cmdArgs:[c.url]);break;
     }
     await flush();
     final deliver=valid&&!(isLinux&&(path=='cold_plugin'||path=='warm_plugin'));
     check(outbound.isEmpty&&outboundIntent!=true,'Duplicate query became outbound: ${c.name}/$path');
     check(deliver?(reports.length==1&&reports.single==c.expectedToken&&reportKeys.length==1&&
       reportKeys.single==c.expectedKey):(reports.isEmpty&&reportKeys.isEmpty),
       'Duplicate query handler payload differs: ${c.name}/$path');
     queryParityAssertions++;
     queryRows.add({'case':c.name,'path':path,'result':deliver?'report':'refuse','noOutbound':true,'mappedPayloadMatches':true});
   }
 }
 var legacyParityAssertions=0;
 for(final entry in <String,String>{
   'mixel-remote://support-other':'support-other',
   'rustdesk://support-other':'support-other',
   'mixel-remote://123456789/r':'123456789/r',
   'mixel-remote://file-transfer/123456789':'123456789',
 }.entries){
   final parsed=urlLinkToCmdArgs(Uri.parse(entry.key));
   check(parsed!=null&&parsed.length==2&&parsed[1]==entry.value,'Legacy parser parity changed');
   legacyParityAssertions++;
   reset();handleUriLink(uriString:entry.key);await flush();
   check(outbound.length==1&&outbound.single==entry.value&&reports.isEmpty,'Legacy handler parity changed');
   legacyParityAssertions++;
 }
 await sub?.cancel();await links.close();_supportInviteAttendedTimer?.cancel();
 print(jsonEncode({'platform':platform,'assertions':assertions,'legacyParityAssertions':legacyParityAssertions,'queryCases':queryCases.length,'queryParityAssertions':queryParityAssertions,'query_results':queryRows,'cases':cases.length,'path_results':rows}));
 print('PASS $platform exact full generated parser/handler and source-extracted initial/plugin/event/boot callers: $assertions assertions; no native, network, TCC or GUI action.');
}
"""


def between(text, begin, end):
    if text.count(begin) != 1:
        raise AssertionError('Changed exact source boundary: ' + begin)
    first = text.index(begin)
    return text[first:text.index(end, first)]


def fixture(common, model, final):
    extracts = {
        'enum': between(common, 'enum UriLinkType {', '\nsetEnvTerminalAdmin()'),
        'initial': between(common, 'Future<bool> initUniLinks() async {', '/// Listen for uni links.'),
        'listener': between(common, 'StreamSubscription? listenUniLinks({', '\nenum UriLinkType'),
        'handler': between(common, 'bool handleUriLink({', '\nList<String>? urlLinkToCmdArgs('),
        'parser': between(common, 'List<String>? urlLinkToCmdArgs(Uri uri) {', '\nconnectMainDesktop('),
        'event': between(model, '  onUrlSchemeReceived(Map<String, dynamic> evt) {', '  /// Bind the event listener'),
    }
    # Extract every legacy branch and all caller prefix guards, unmodified.
    tests = DART_TESTS
    if final:
        tests = tests.replace("apikey=$apiKey','legacy-support')", "apikey=$apiKey','refuse')")
        tests = tests.replace("path=='rust_event'&&!c.url.startsWith(bind.mainUriPrefixSync())",
                              "path=='rust_event'&&!c.url.toLowerCase().startsWith(bind.mainUriPrefixSync().toLowerCase())")
    return DART_PRELUDE + '\n'.join(extracts.values()) + tests, extracts


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_symlink():
            result[relative] = ('symlink', os.readlink(path), info.st_nlink)
        elif path.is_file():
            result[relative] = ('file', hashlib.sha256(path.read_bytes()).hexdigest(), info.st_nlink)
        elif path.is_dir():
            result[relative] = ('directory',)
        else:
            result[relative] = ('nonregular', info.st_mode)
    return result


class UriRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        check = patch.apply(SOURCE, check=True, restore=True)
        if check['profile'] == 'fresh':
            raise AssertionError('Tests require a complete branded source, not fresh upstream')
        cls.original = {}
        cls.final = {}
        for name in patch.ORIGINAL:
            text, _ = patch.canonical(patch.safe_source(SOURCE, name).read_bytes())
            cls.original[name] = patch.transform(name, text, True)
            cls.final[name] = patch.transform(name, cls.original[name])
        cls.native_original = {}
        cls.native_final = {}
        for name in patch.NATIVE_ORIGINAL:
            text, _ = patch.canonical(patch.safe_source(SOURCE, name).read_bytes())
            cls.native_original[name] = patch.transform_native(name, text, True)
            cls.native_final[name] = patch.transform_native(name, cls.native_original[name])
        cls.prior = patch.prior_namespace()
        cls.compiler = patch.prior_compiler(cls.prior)
        cls.reporter = (SOURCE / 'flutter/lib/mixel_support_invite.dart').read_bytes()
        cls.bindings = {'inputProfile': check['profile'], 'original': patch.ORIGINAL,
                        'final': patch.FINAL, 'fresh': patch.FRESH, 'priorVerifier': patch.PRIOR_SHA,
                        'uriOwner': hashlib.sha256((BASE / 'patch-support-uri-routing.py').read_bytes()).hexdigest(),
                        'uriTest': hashlib.sha256((BASE / 'test-support-uri-routing.py').read_bytes()).hexdigest(),
                        'reporter': hashlib.sha256(cls.reporter).hexdigest()}
        (OUTPUT / 'source-bindings.json').write_text(json.dumps(cls.bindings, indent=2) + '\n')

    def pure(self, root, newline='\n', final=False):
        root.mkdir()
        combined = dict(self.final if final else self.original)
        combined.update(self.native_final if final else self.native_original)
        for name, text in combined.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.replace('\n', newline).encode())
        return root

    def test_01_full_source_routing_all_platform_callers_original_and_corrected(self):
        counts = []
        for final in [False, True]:
            name = 'corrected' if final else 'original'
            directory = OUTPUT / name
            directory.mkdir()
            texts = self.final if final else self.original
            source, extracts = fixture(texts['flutter/lib/common.dart'], texts['flutter/lib/models/model.dart'], final)
            dart_file = directory / 'routing.dart'
            dart_file.write_text(source)
            (directory / 'mixel_support_invite.dart').write_bytes(self.reporter)
            for key, text in extracts.items():
                (directory / (key + '.dart.txt')).write_text(text)
            for platform in ['macos', 'linux', 'windows']:
                result = subprocess.run([str(DART), str(dart_file), platform], capture_output=True,
                                        text=True, timeout=30)
                (directory / (platform + '.log')).write_text(result.stdout + result.stderr)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout.splitlines()[0])
                self.assertEqual(data['assertions'], 108)
                self.assertEqual(data['legacyParityAssertions'], 8)
                self.assertEqual(data['queryCases'], 24)
                self.assertEqual(data['queryParityAssertions'], 144)
                self.assertEqual(len(data['query_results']), 120)
                self.assertTrue(all(row['noOutbound'] and row['mappedPayloadMatches'] for row in data['query_results']))
                rows = {(row['case'], row['path']): row['result'] for row in data['path_results']}
                self.assertEqual(rows['valid_product', 'direct'], 'report')
                self.assertEqual(rows['wrong_rustdesk_scheme', 'direct'], 'refuse' if final else 'legacy-support')
                self.assertEqual(rows['mixed_case_product', 'rust_event'], 'report' if final else 'refuse')
                counts.append(data['assertions'])
        self.assertEqual(sum(counts), 648)
        print('PASS 648 exact full parser/handler/caller assertions plus 48 numeric/non-support/transfer legacy parity assertions; baseline defects reproduced; corrected routes pass.')
        print('PASS 864 duplicate-query parser/payload/caller assertions; 24 same-key/mixed-case/encoded-alias/value-order cases, valid mapped reports only and invalid refusal with zero outbound requests.')

    def test_02_lf_crlf_idempotence_and_exact_inverse(self):
        for newline in ['\n', '\r\n']:
            with self.subTest(newline=repr(newline)), tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                root = self.pure(Path(temp) / 'source', newline)
                original = snapshot(root)
                self.assertEqual(patch.apply(root, check=True, verify=False)['writes'], 0)
                self.assertEqual(snapshot(root), original)
                self.assertEqual(patch.apply(root, verify=False)['writes'], 2)
                corrected = snapshot(root)
                self.assertEqual(patch.apply(root, verify=False)['writes'], 0)
                self.assertEqual(snapshot(root), corrected)
                self.assertEqual(patch.apply(root, check=True, restore=True, verify=False)['writes'], 0)
                self.assertEqual(snapshot(root), corrected)
                self.assertEqual(patch.apply(root, restore=True, verify=False)['writes'], 2)
                self.assertEqual(patch.apply(root, restore=True, verify=False)['writes'], 0)
                self.assertEqual(snapshot(root), original)

    def test_03_all_strict_refusals_are_zero_write(self):
        variants = ['changed-common', 'changed-model', 'partial-common', 'partial-model', 'missing',
                    'mixed-common', 'mixed-model', 'bare-cr', 'invalid-utf8', 'empty', 'symlink',
                    'hardlink', 'symlink-parent', 'symlink-root']
        refusals = 0
        for variant in variants:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                base = Path(temp)
                root = self.pure(base / 'source')
                common = root / 'flutter/lib/common.dart'
                model = root / 'flutter/lib/models/model.dart'
                if variant == 'changed-common': common.write_bytes(common.read_bytes() + b'// changed\n')
                elif variant == 'changed-model': model.write_bytes(model.read_bytes() + b'// changed\n')
                elif variant == 'partial-common': common.write_text(self.final['flutter/lib/common.dart'])
                elif variant == 'partial-model': model.write_text(self.final['flutter/lib/models/model.dart'])
                elif variant == 'missing': model.unlink()
                elif variant == 'mixed-common': common.write_bytes(common.read_bytes().replace(b'\n', b'\r\n', 1))
                elif variant == 'mixed-model': model.write_bytes(model.read_bytes().replace(b'\n', b'\r\n', 1))
                elif variant == 'bare-cr': model.write_bytes(model.read_bytes().replace(b'\n', b'\r', 1))
                elif variant == 'invalid-utf8': model.write_bytes(model.read_bytes() + b'\xff')
                elif variant == 'empty': model.write_bytes(b'')
                elif variant == 'hardlink': os.link(common, root / 'linked')
                elif variant in ['symlink', 'symlink-parent', 'symlink-root']:
                    try:
                        if variant == 'symlink':
                            common.rename(root / 'held-common'); common.symlink_to(root / 'held-common')
                        elif variant == 'symlink-parent':
                            (root / 'flutter/lib/models').rename(root / 'held-models')
                            (root / 'flutter/lib/models').symlink_to(root / 'held-models', target_is_directory=True)
                        else:
                            root.rename(base / 'held-root'); root.symlink_to(base / 'held-root', target_is_directory=True)
                    except OSError:
                        if os.name == 'nt': continue  # Platform cannot create a test symlink; other guards remain mandatory.
                        raise
                before = snapshot(base)
                for check, restore in [(False, False), (True, False), (False, True), (True, True)]:
                    with self.assertRaises((RuntimeError, OSError, UnicodeError)):
                        patch.apply(root, check=check, restore=restore, verify=False)
                    self.assertEqual(snapshot(base), before)
                    refusals += 1
        print('PASS %d actual malformed/partial/newline/ownership refusals; every call leaves complete fixture inventory unchanged.' % refusals)

    def test_04_prior_ownership_and_verifier_refuse_before_copy(self):
        for kind in ['hardlink', 'symlink', 'fifo']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                root = self.pure(Path(temp) / 'source')
                path = root / 'unrelated-source.txt'
                path.write_text('owned')
                if kind == 'hardlink': os.link(path, root / 'unrelated-alias.txt')
                elif kind == 'symlink':
                    try: path.symlink_to(root / 'flutter/lib/common.dart')
                    except FileExistsError:
                        path.unlink()
                        try: path.symlink_to(root / 'flutter/lib/common.dart')
                        except OSError:
                            if os.name == 'nt': continue
                            raise
                else:
                    if not hasattr(os, 'mkfifo'): continue
                    path.unlink(); os.mkfifo(path)
                before = snapshot(root)
                with self.assertRaises(RuntimeError): patch.apply(root, verify=True)
                self.assertEqual(snapshot(root), before)
        with tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
            directory = Path(temp)
            (directory / patch.PRIOR_NAME).write_text((BASE / patch.PRIOR_NAME).read_text() + '# drift\n')
            old = patch.SCRIPTS
            patch.SCRIPTS = directory
            try:
                before = snapshot(directory)
                with self.assertRaises(RuntimeError): patch.prior_namespace()
                self.assertEqual(snapshot(directory), before)
            finally: patch.SCRIPTS = old

    def test_05_actual_complete_prior_final_projection_and_conservation(self):
        self.compiler['validate_projection_sources'](SOURCE)
        with tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
            root = Path(temp) / 'source'
            shutil.copytree(SOURCE, root, symlinks=True,
                            ignore=lambda directory, names: self.compiler['projection_ignored'](SOURCE, directory, names))
            patch.apply(root, restore=True, verify=False)
            before = snapshot(root)
            native = self.prior['DEPENDENCY']
            native_before = snapshot(root / native)
            self.assertEqual(patch.apply(root, verify=True)['writes'], 2)
            final = snapshot(root)
            all_changed = {name for name in set(before) | set(final) if before.get(name) != final.get(name)}
            changed = all_changed & set(patch.ORIGINAL)
            self.assertEqual(changed, set(patch.ORIGINAL))
            self.assertEqual(all_changed - changed, set(patch.NATIVE_ORIGINAL))
            self.assertEqual(snapshot(root / native), native_before)
            self.assertEqual(patch.apply(root, check=True, restore=True, verify=True)['profile'], 'final')
            self.assertEqual(snapshot(root), final)
            with patch.previous(root) as previous:
                self.assertNotEqual(previous, root)
                self.assertEqual(snapshot(previous), before)
            self.assertEqual(snapshot(root), final)
            patch.apply(root, restore=True, verify=False)
            self.assertEqual(snapshot(root), before)
            # Change a genuine older owned source, with safe regular ownership.
            old_source = root / 'src/server.rs'
            old_source.write_bytes(old_source.read_bytes() + b'// prior source drift\n')
            drifted = snapshot(root)
            with self.assertRaises(RuntimeError): patch.apply(root, verify=True)
            self.assertEqual(snapshot(root), drifted)
        print('PASS complete actual prior-owner chain, final projection, exact two-Dart/five-native-source changes, native dependency-family conservation, older-source refusal, supplied-tree conservation.')

    def test_06_prior_fresh_pins_and_cli_flags(self):
        linux = self.prior['prior_namespace']()
        self.assertEqual(patch.FRESH['flutter/lib/common.dart'], linux['FRESH']['flutter/lib/common.dart'])
        self.assertEqual(patch.FRESH['flutter/lib/models/model.dart'], patch.ORIGINAL['flutter/lib/models/model.dart'])
        for name, original in self.original.items():
            self.assertEqual(patch.digest(original.encode()), patch.ORIGINAL[name])
            self.assertEqual(patch.digest(patch.transform(name, original).encode()), patch.FINAL[name])
        with tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
            root = self.pure(Path(temp) / 'source')
            before = snapshot(root)
            for flags in [['--before-branding'], ['--after-branding', '--restore']]:
                result = subprocess.run([os.sys.executable, str(BASE / 'patch-support-uri-routing.py'),
                                         '--root', str(root), *flags], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(snapshot(root), before)

    def test_07_presence_guard_and_actual_historical_consumers_before_copy(self):
        historical_spec = importlib.util.spec_from_file_location('uri_actual_historical_consumers', BASE / 'test-support-flutter-analysis.py')
        historical = importlib.util.module_from_spec(historical_spec)
        historical_spec.loader.exec_module(historical)
        linux = self.prior['prior_namespace']()
        fresh_raw = subprocess.check_output(['git', '-C', str(GIT_SOURCE), 'show', linux['PIN'] + ':flutter/lib/common.dart'])
        fresh, _ = patch.canonical(fresh_raw)
        self.assertEqual(patch.digest(fresh.encode()), patch.FRESH['flutter/lib/common.dart'])
        older = linux['transform']('flutter/lib/common.dart', self.original['flutter/lib/common.dart'], True)
        self.assertEqual(patch.digest(older.encode()), linux['ORIGINAL']['flutter/lib/common.dart'])
        commons = {'fresh': fresh, 'older-linux': older,
                   'original': self.original['flutter/lib/common.dart'],
                   'final': self.final['flutter/lib/common.dart']}
        models = {'original': self.original['flutter/lib/models/model.dart'],
                  'final': self.final['flutter/lib/models/model.dart']}
        bad_models = {'appended-drift': models['final'] + '// unrelated drift\n',
                      'marker-removed': models['final'].replace(patch.CHANGES['flutter/lib/models/model.dart'][0][1], '    if (false &&\n', 1)}
        guard_calls = 0
        for newline in ['\n', '\r\n']:
            for common_name, common in commons.items():
                for model_name, model in models.items():
                    with self.subTest(valid=(repr(newline), common_name, model_name)), tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                        root = self.pure(Path(temp) / 'source', newline)
                        (root / 'flutter/lib/common.dart').write_bytes(common.replace('\n', newline).encode())
                        (root / 'flutter/lib/models/model.dart').write_bytes(model.replace('\n', newline).encode())
                        before = snapshot(root)
                        self.assertEqual(patch.has_layer(root), common_name == 'final' or model_name == 'final')
                        self.assertEqual(snapshot(root), before)
                for model_name, model in bad_models.items():
                    with self.subTest(invalid=(repr(newline), common_name, model_name)), tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                        root = self.pure(Path(temp) / 'source', newline)
                        (root / 'flutter/lib/common.dart').write_bytes(common.replace('\n', newline).encode())
                        (root / 'flutter/lib/models/model.dart').write_bytes(model.replace('\n', newline).encode())
                        before = snapshot(root)
                        with self.assertRaisesRegex(RuntimeError, 'Changed URI routing model presence source'):
                            patch.has_layer(root)
                        guard_calls += 1
                        for consumer in [historical.linux_close_baseline, historical.startup_baseline]:
                            with mock.patch.object(shutil, 'copytree', side_effect=AssertionError('Unsafe source reached copy')) as copier:
                                with self.assertRaisesRegex(RuntimeError, 'Changed URI routing model presence source'):
                                    # These are deliberately old two/seven-file unit fixtures;
                                    # the complete actual SOURCE was checked at CLI admission.
                                    with consumer(root, _input_checked=True):
                                        self.fail('Unsafe source reached historical consumer body')
                                copier.assert_not_called()
                            self.assertEqual(snapshot(root), before)
                            guard_calls += 1
        self.assertEqual(guard_calls, 48)
        print('PASS 48 LF/CRLF malformed-model has_layer and actual historical-consumer refusals before any copy; fresh/older/original/final presence behavior conserved.')


    def test_08_native_source_lf_crlf_migration_and_exact_inverse(self):
        for newline in ['\n', '\r\n']:
            with self.subTest(newline=repr(newline)), tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                root = self.pure(Path(temp) / 'source', newline)
                before = snapshot(root)
                # Preserve and execute the exact previous-final warm migration:
                # old Dart terminal changes exist, native correction is absent.
                for name, text in self.final.items():
                    (root / name).write_bytes(text.replace('\n', newline).encode())
                previous_final = snapshot(root)
                status = patch.apply(root, check=True, restore=True, verify=False)
                self.assertEqual((status['profile'], status['nativeWrites']), ('previous-final', 0))
                self.assertEqual(snapshot(root), previous_final)
                with self.assertRaisesRegex(RuntimeError, 'restore-only'):
                    patch.apply(root, verify=False)
                self.assertEqual(snapshot(root), previous_final)
                patch.apply(root, restore=True, verify=False)
                self.assertEqual(snapshot(root), before)
                status = patch.apply(root, verify=False)
                self.assertEqual((status['profile'], status['writes'], status['nativeWrites']), ('original', 2, 5))
                final = snapshot(root)
                status = patch.apply(root, verify=False)
                self.assertEqual((status['profile'], status['writes'], status['nativeWrites']), ('final', 0, 0))
                self.assertEqual(snapshot(root), final)
                patch.apply(root, restore=True, verify=False)
                self.assertEqual(snapshot(root), before)
                status = patch.apply(root, verify=False)
                self.assertEqual((status['writes'], status['nativeWrites']), (2, 5))
                self.assertEqual(snapshot(root), final)
        print('PASS exact old-final warm migration, LF/CRLF native idempotence and whole-file inverse; all original Dart assertions retained.')

    def test_09_native_whole_profile_refusals_before_any_write(self):
        refusals = 0
        for name in patch.NATIVE_ORIGINAL:
            for variant in ['append', 'partial', 'missing', 'mixed-newline', 'invalid-utf8', 'hardlink']:
                with self.subTest(name=name, variant=variant), tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                    root = self.pure(Path(temp) / 'source')
                    path = root / name
                    if variant == 'append': path.write_bytes(path.read_bytes() + b'// drift\n')
                    elif variant == 'partial': path.write_text(self.native_final[name])
                    elif variant == 'missing': path.unlink()
                    elif variant == 'mixed-newline': path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n', 1))
                    elif variant == 'invalid-utf8': path.write_bytes(path.read_bytes() + b'\xff')
                    else: os.link(path, root / 'unexpected-native-link')
                    before = snapshot(root)
                    for check, restore in [(False, False), (True, False), (False, True), (True, True)]:
                        with self.assertRaises((RuntimeError, OSError, UnicodeError)):
                            patch.apply(root, check=check, restore=restore, verify=False)
                        self.assertEqual(snapshot(root), before)
                        refusals += 1
        self.assertEqual(refusals, 120)
        print('PASS 120 exact native drift/partial/missing/newline/encoding/ownership refusals before any write.')

    def test_10_actual_native_sender_common_core_and_receiver_boundaries(self):
        directory = OUTPUT / 'actual-native-boundaries'
        result = subprocess.run([os.sys.executable, str(BASE / 'test-support-native-uri-boundary.py'),
                                 '--source', str(SOURCE), '--output', str(directory)],
                                capture_output=True, text=True, timeout=120)
        (OUTPUT / 'actual-native-boundaries.log').write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('test result: ok.', result.stdout)
        print(result.stdout.strip())


    def test_11_exact_fresh_missing_queue_and_mixed_profiles(self):
        for newline in ['\n', '\r\n']:
            with self.subTest(newline=repr(newline)), tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                root = self.pure(Path(temp) / 'source', newline)
                for name in (*patch.FRESH, *patch.NATIVE_FRESH):
                    expected = patch.FRESH.get(name, patch.NATIVE_FRESH.get(name))
                    path = root / name
                    if expected is None:
                        path.unlink()
                        continue
                    raw = subprocess.check_output(['git', '-C', str(GIT_SOURCE), 'show', self.prior['prior_namespace']()['PIN'] + ':' + name])
                    logical, _ = patch.canonical(raw)
                    self.assertEqual(patch.digest(logical.encode()), expected)
                    path.write_bytes(logical.replace('\n', newline).encode())
                before = snapshot(root)
                status = patch.apply(root, check=True, restore=True, verify=False)
                self.assertEqual((status['profile'], status['writes'], status['nativeWrites']), ('fresh', 0, 0))
                patch.apply(root, restore=True, verify=False)
                self.assertEqual(snapshot(root), before)
                with self.assertRaisesRegex(RuntimeError, 'requires complete branding'):
                    patch.apply(root, verify=False)
                self.assertEqual(snapshot(root), before)
                # A module at this fresh upstream stage is a partial mixed tree,
                # even if its bytes are a genuine lower-layer module.
                path = root / 'src/mixel_support_url.rs'
                path.write_bytes(self.native_original['src/mixel_support_url.rs'].replace('\n', newline).encode())
                before = snapshot(root)
                with self.assertRaises(RuntimeError):
                    patch.apply(root, restore=True, check=True, verify=False)
                self.assertEqual(snapshot(root), before)
            with tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
                root = self.pure(Path(temp) / 'source', newline)
                for name, text in self.native_final.items():
                    (root / name).write_bytes(text.replace('\n', newline).encode())
                before = snapshot(root)
                for check, restore in [(False, False), (True, False), (False, True), (True, True)]:
                    with self.assertRaisesRegex(RuntimeError, 'Mixed Dart/native'):
                        patch.apply(root, check=check, restore=restore, verify=False)
                    self.assertEqual(snapshot(root), before)
        print('PASS exact LF/CRLF fresh absent-queue inventory and mixed-profile zero-write refusals; old-final restore-only remains separately exercised.')

    def test_12_cold_standard_guard_is_terminal_and_whole_source_reversible(self):
        name = 'src/core_main.rs'
        self.assertEqual(len(patch.NATIVE_CHANGES[name]), 3)
        before, after = patch.NATIVE_CHANGES[name][-1]
        self.assertEqual(after.count('crate::platform::is_elevated(None).unwrap_or(false)'), 1)
        self.assertTrue(after.startswith(before))
        self.assertIn('!_is_elevate\n        && !_is_run_as_system\n', before)
        self.assertIn('_mixel_attended_handoff_confirmed && mixel_has_same_executable_main_window()', before)
        previous = self.native_final[name].replace(after, before, 1)
        self.assertEqual(hashlib.sha256(previous.encode()).hexdigest(),
                         'ae088f7f431712968424f0e8d842c00c746c1fd9ab28533b579d4327181ca2ee')
        self.assertEqual(patch.transform_native(name, self.native_final[name], True), self.native_original[name])
        self.assertEqual(patch.transform_native(name, self.native_original[name]), self.native_final[name])
        for mutation in (self.native_final[name].replace(after, before, 1),
                         self.native_final[name].replace(after, after + after, 1),
                         self.native_final[name].replace('unwrap_or(false)\n    {\n        use crate::portable_service',
                                                       'unwrap_or(true)\n    {\n        use crate::portable_service', 1)):
            self.assertNotEqual(mutation, self.native_final[name])
            with self.assertRaises(RuntimeError):
                patch.transform_native(name, mutation, True)
        # Existing LF/CRLF full-chain tests run this final owner after the
        # unchanged crash/Mac-launch owners and restore it before those owners.
        print('PASS terminal cold Standard guard, fail-closed token query, explicit elevation/system paths conserved; exact new/previous/full-prior source inverses and missing/duplicate/drift refusals.')


class ColdWindowsControls(unittest.TestCase):
    """Execute only extracted PowerShell predicates, with no native API calls."""

    def invoke(self, body, names):
        pwsh = shutil.which('pwsh')
        if not pwsh:
            self.skipTest('PowerShell predicate engine unavailable; Windows CI remains required')
        with tempfile.TemporaryDirectory(prefix='mixel-cold-source-controls-') as temporary:
            root = Path(temporary)
            script = root / 'control.ps1'
            prefix = r'''
$ErrorActionPreference='Stop'
$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$t,[ref]$e)
if($e.Count){throw 'Complete cold test does not parse'}
foreach($name in __NAMES__){
 $found=@($ast.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $name})
 if($found.Count-ne 1){throw 'Exact predicate extraction failed'}
 . ([scriptblock]::Create($found[0].Extent.Text))
}
'''.replace('__SOURCE__', str(BASE / 'test-support-cold-windows.ps1').replace("'", "''"))
            prefix = prefix.replace('__NAMES__', '@(' + ','.join("'" + name + "'" for name in names) + ')')
            script.write_text(prefix + body, encoding='utf-8-sig')
            completed = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-File', str(script)],
                                       capture_output=True, text=True, encoding='utf-8', timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn('PASS:', completed.stdout)

    def test_actual_identity_rejects_high_foreign_pid_sid_session_path_and_birth(self):
        self.invoke(r'''
foreach($case in @('valid','pid','sid','session','high','integrity','birth','path')){
 $f=[pscustomobject]@{pid=42;sid='synthetic-owned';session=2;elevated=$false;integrity=8192;birth=100;path='owned.exe'}
 switch($case){'pid'{$f.pid=43};'sid'{$f.sid='foreign'};'session'{$f.session=3};'high'{$f.elevated=$true};'integrity'{$f.integrity=12288};'birth'{$f.birth=0};'path'{$f.path='foreign.exe'}}
 $failed=$false;try{Assert-ColdIdentity $f 42 'synthetic-owned' 2 'owned.exe'}catch{$failed=$true}
 if($failed-ne ($case-cne 'valid')){throw ('Wrong identity result '+$case)}
}
'PASS: actual cold identity predicate8 Standard/Medium/foreign/high controls.'
''', ['Assert-ColdIdentity'])

    def test_actual_health_requires_stable_incoming_id_relay_and_v2(self):
        self.invoke(r'''
foreach($case in @('valid','pid','ready','proof','relay','offline','key','server','id')){
 $h=[pscustomobject]@{incomingPid=42;attendedReady=$true;attendedProof='attended-runtime-v2';brandedRelay=$true;rendezvousState=1;keyConfirmed=$true;rendezvousServer='rs.mixel.ch:21116';registeredId='synthetic-id'}
 switch($case){'pid'{$h.incomingPid=43};'ready'{$h.attendedReady=$false};'proof'{$h.attendedProof='guard-unavailable'};'relay'{$h.brandedRelay=$false};'offline'{$h.rendezvousState=0};'key'{$h.keyConfirmed=$false};'server'{$h.rendezvousServer='foreign'};'id'{$h.registeredId='changed'}}
 $failed=$false;try{Assert-ColdHealth $h 42 'synthetic-id'}catch{$failed=$true}
 if($failed-ne ($case-cne 'valid')){throw ('Wrong native health result '+$case)}
}
'PASS: actual cold health predicate9 PID/stableID/relay/key/v2 controls.'
''', ['Assert-ColdHealth'])

    def test_actor_is_exact_pinned_ast_without_original_top_level_execution(self):
        self.invoke(r'''
$source=Read-ColdPinnedText '__LAUNCHER__' 'c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b'
$t=$null;$e=$null;$actor=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$t,[ref]$e)
if($e.Count){throw 'Actor source parse failed'}
foreach($type in @('MixelSupportWindowTest','MixelOrdinaryTokenFixture','MixelPackagedQaTokenFacts')){
 $found=@($actor.FindAll({param($n)$n -is [Management.Automation.Language.StringConstantExpressionAst] -and $n.Value.Contains('public static class '+$type+' {')},$true))
 if($found.Count-ne 1){throw 'Actor class literal ambiguity'}
}
foreach($name in @('Start-OwnedPackagedQaNamespace','Restore-OwnedPackagedQaNamespace','Assert-OwnedPackagedQaCleanupBeforeDeletion')){
 $found=@($actor.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.FunctionDefinitionAst]-and $_.Name-ceq $name})
 if($found.Count-ne 1){throw 'Namespace predicate ambiguity'}
}
'PASS: whole pinned actor and exact three class/three function AST inputs; no Add-Type or top-level launch executed.'
'''.replace('__LAUNCHER__', str(BASE / 'test-support-launch-windows.ps1').replace("'", "''")), ['Read-ColdPinnedText'])

    def test_actual_process_event_gate_refuses_uac_foreign_and_bound(self):
        # Only process-token capture is an API stand-in; the event-loop body,
        # identity predicate and its finally-removal execute as actual source.
        source = (BASE / 'test-support-cold-windows.ps1').read_text()
        self.assertEqual(source.count('[MixelColdProcessObserver]::Capture($child)'), 1)
        self.invoke(r'''
$fn=(Get-Command Read-ColdStarts).ScriptBlock.ToString()
$fn=$fn.Replace('[MixelColdProcessObserver]::Capture($child)','(Get-ColdMockFacts $child)')
Set-Item Function:Read-ColdStarts ([scriptblock]::Create($fn))
function Get-ColdMockFacts($id){$script:queries++;[pscustomobject]@{pid=$id;sid='owned';session=2;elevated=$script:high;integrity=8192;birth=1;path='owned.exe'}}
function Get-FileHash{[pscustomobject]@{Hash='HASH'}}
function Get-Event{param($SourceIdentifier,$ErrorAction);return $script:event}
function Remove-Event{param($EventIdentifier,$ErrorAction);$script:removed++}
foreach($case in @('outer','child','uac','foreign','high','overflow')){
 $script:queries=0;$script:removed=0;$script:high=($case-ceq 'high');$outerPid=41;$ownedSid='owned';$callerSession=2;$runtimePath='owned.exe';$expectedMainHash='HASH';$eventSource='synthetic'
 $events=[Collections.Generic.List[object]]::new();if($case-ceq 'overflow'){for($i=0;$i-lt 512;$i++){$events.Add([object]$i)}}
 $name=if($case-ceq 'uac'){'consent.exe'}elseif($case-ceq 'foreign'){'foreign.exe'}else{'Mixel-Remote.exe'}
 $id=if($case-ceq 'outer'){41}else{42}
 $script:event=[pscustomobject]@{EventIdentifier=1;SourceEventArgs=[pscustomobject]@{NewEvent=[pscustomobject]@{ProcessName=$name;ProcessID=$id;ParentProcessID=41;SessionID=2}}}
 $failed=$false;try{Read-ColdStarts}catch{$failed=$true}
 if($failed-ne ($case-notin @('outer','child'))-or $script:removed-ne 1){throw ('Wrong actual event outcome '+$case)}
 if($case-in @('uac','foreign','overflow','outer')-and $script:queries-ne 0){throw 'Forbidden native token query reached'}
}
'PASS: actual event gate6 controls; UAC/foreign/overflow refuse, Standard child accepted, event removal always runs.'
''', ['Read-ColdStarts', 'Assert-ColdIdentity'])

    def test_actual_positive_event_witness_requires_delivery_time_session_and_ancestry(self):
        self.invoke(r'''
foreach($case in @('valid','empty','missing-main','time','session','parent','duplicate','cycle')){
 $outer=[pscustomobject]@{name='Mixel-Remote-QS.exe';pid=41;parent=1;session=2;birth=100;timeCreated=101}
 $main=[pscustomobject]@{name='Mixel-Remote.exe';pid=42;parent=41;session=2;birth=102;timeCreated=103}
 $rows=@($outer,$main)
 switch($case){'empty'{$rows=@()};'missing-main'{$rows=@($outer)};'time'{$main.timeCreated=1};'session'{$main.session=3};'parent'{$main.parent=99};'duplicate'{$rows=@($outer,$main,$main)};'cycle'{$main.parent=42}}
 $failed=$false;try{Assert-ColdStartWitnesses $rows 41 42 2}catch{$failed=$true}
 if($failed-ne ($case-cne 'valid')){throw ('Wrong witness outcome '+$case)}
}
'PASS: actual positive WMI witness8 controls; empty/blind subscription, PID reuse/time, foreign session/ancestry and cycles refuse.'
''', ['Assert-ColdStartWitnesses'])

    def test_actual_auth0_reply_refuses_foreign_server_and_coercible_counts(self):
        self.invoke(r'''
$fn=(Get-Command Assert-ColdUnauthenticatedRemote).ScriptBlock.ToString()
$fn=$fn.Replace('[MixelSupportIpcProbe]::RequestWithServerPid(', '(Get-ColdReply ')
Set-Item Function:Assert-ColdUnauthenticatedRemote ([scriptblock]::Create($fn))
function Get-ColdReply($request){if($request-cne '{"t":"VideoConnCount","c":null}'){throw 'Wrong read-only IPC request'};return $script:reply}
foreach($case in @('zero','active','bool','string','array','null','kind','pid')){
 $script:reply=[pscustomobject]@{ServerPid=42;Json='{"t":"VideoConnCount","c":0}'}
 switch($case){'active'{$reply.Json='{"t":"VideoConnCount","c":1}'};'bool'{$reply.Json='{"t":"VideoConnCount","c":false}'};'string'{$reply.Json='{"t":"VideoConnCount","c":"0"}'};'array'{$reply.Json='{"t":"VideoConnCount","c":[0]}'};'null'{$reply.Json='{"t":"VideoConnCount","c":null}'};'kind'{$reply.Json='{"t":"Other","c":0}'};'pid'{$reply.ServerPid=43}}
 $failed=$false;try{Assert-ColdUnauthenticatedRemote 42}catch{$failed=$true}
 if($failed-ne ($case-cne 'zero')){throw ('Wrong actual auth0 outcome '+$case)}
}
'PASS: actual same-server VideoConnCount8 controls reject authenticated peers, wrong PID/type and coercible zero.'
''', ['Assert-ColdUnauthenticatedRemote'])

    def test_frozen_launcher_and_cold_gate_workflow_required_order(self):
        workflow = (BASE.parent / '.github/workflows/build.yml').read_text()
        cold = '      - name: Prove cold signed QuickSupport starts as a fresh Standard user without UAC\n'
        warm = '      - name: Exercise the signed QuickSupport download without an invite\n'
        self.assertEqual(workflow.count(cold), 1)
        self.assertLess(workflow.index('      - name: Verify Windows Authenticode signatures\n'), workflow.index(cold))
        self.assertLess(workflow.index(cold), workflow.index(warm))
        step = workflow.split(cold, 1)[1].split('\n      - ', 1)[0]
        self.assertNotIn('continue-on-error', step)
        self.assertNotIn('if:', step)
        self.assertIn('-Executable dist/Mixel-Remote-QS.exe -ExpectedPayload store-payload', step)
        self.assertIn('mixel-owned-ordinary-qs-logs/cold-support/*.json', workflow)
        raw = (BASE / 'test-support-launch-windows.ps1').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(hashlib.sha256(raw).hexdigest(), 'c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b')


def main():
    global SOURCE, GIT_SOURCE, OUTPUT, DART
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--dart', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    SOURCE = args.source.absolute()
    GIT_SOURCE = SOURCE
    DART = args.dart.absolute()
    OUTPUT = args.output.absolute()
    OUTPUT.mkdir(mode=0o700)
    with input_lifecycle.current_source(SOURCE) as previous_input:
        SOURCE = previous_input
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UriRoutingTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    if os.sys.argv[1:] == ['--cold-source-controls']:
        unittest.main(argv=[os.sys.argv[0]], defaultTest='ColdWindowsControls', verbosity=2)
    else:
        main()
