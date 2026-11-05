#!/usr/bin/env python3
"""Execute the exact generated Ruby hook against inert and real Xcodeproj objects.

No CocoaPods command, Flutter, compiler, SDK, app, provider or native API is invoked.
The complete original Podfile is pinned to upstream1abc; other bytes stay exact.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('macos_pod_minimum_owner', HERE / 'patch-support-macos.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
ORIGINAL = (HERE / 'fixtures/macos-pod-minimum/Podfile').read_text()
RUBY = r'''
Config = Struct.new(:build_settings, :name, :project, :base_configuration_reference) do
  def resolve_build_setting(key, _target)
    value = build_settings[key]
    return '' if ['$(inherited)', '${inherited}'].include?(value)
    value
  end
end
Target = Struct.new(:build_configurations)
Project = Struct.new(:targets, :build_configuration_list)
Installer = Struct.new(:pods_project)
data = JSON.parse(ARGV.fetch(0))
$helper_minimum = data['helperMinimum']
$flutter_calls = 0
project = Project.new([], {})
targets = data.fetch('targets').map do |versions|
  Target.new(versions.each_with_index.map { |value, index|
    Config.new({'MACOSX_DEPLOYMENT_TARGET' => value}, index.to_s, project, nil)
  })
end
project.targets = targets
$installer = Installer.new(project)
def flutter_additional_macos_build_settings(target)
  $flutter_calls += 1
  unless $helper_minimum.nil?
    target.build_configurations.each do |configuration|
      configuration.build_settings['MACOSX_DEPLOYMENT_TARGET'] = $helper_minimum
    end
  end
end
def post_install
  yield $installer
end
eval(STDIN.read, TOPLEVEL_BINDING, 'exact-generated-pod-post-install.rb')
puts JSON.generate({'versions' => targets.map { |target|
  target.build_configurations.map { |configuration| configuration.build_settings['MACOSX_DEPLOYMENT_TARGET'] }
}, 'flutterCalls' => $flutter_calls})
'''

XCODEPROJ_RUBY = r'''
require 'xcodeproj'
data = JSON.parse(ARGV.fetch(0))
directory = data.fetch('directory')
project = Xcodeproj::Project.new(File.join(directory, 'Pods.xcodeproj'))
target = project.new_target(:static_library, 'MixelPodMinimumFixture', :osx, '10.13')
key = 'MACOSX_DEPLOYMENT_TARGET'
project.build_configurations.each do |configuration|
  configuration.build_settings.delete(key)
  configuration.build_settings[key] = data['projectDirect'] if data['projectDirect']
  if data['projectBase']
    path = File.join(directory, "Project-#{configuration.name}.xcconfig")
    File.write(path, "#{key} = #{data['projectBase']}\n")
    configuration.base_configuration_reference = project.new_file(path)
  end
end
target.build_configurations.each do |configuration|
  configuration.build_settings.delete(key)
  configuration.build_settings[key] = data['direct'] if data['direct']
  if data['base'] || data['includedBase']
    path = File.join(directory, "Target-#{configuration.name}.xcconfig")
    contents = data['base'] ? "#{key} = #{data['base']}\n" : ''
    if data['includedBase']
      included = File.join(directory, "Included-#{configuration.name}.xcconfig")
      File.write(included, "#{key} = #{data['includedBase']}\n")
      contents = "#include \"#{File.basename(included)}\"\n" + contents
    end
    File.write(path, contents)
    configuration.base_configuration_reference = project.new_file(path)
  end
end
$installer = Struct.new(:pods_project).new(project)
$helper_minimum = data['helperMinimum']
def flutter_additional_macos_build_settings(target)
  return if $helper_minimum.nil?
  target.build_configurations.each do |configuration|
    configuration.build_settings['MACOSX_DEPLOYMENT_TARGET'] = $helper_minimum
  end
end
def post_install
  yield $installer
end
before = target.build_configurations.map { |configuration| configuration.resolve_build_setting(key, target) }
begin
  eval(STDIN.read, TOPLEVEL_BINDING, 'exact-generated-pod-post-install.rb')
rescue StandardError => error
  warn "#{error.class}: #{error.message}"
  puts JSON.generate({'environmentAfter' => ENV[key]})
  exit 1
end
puts JSON.generate({'versions' => target.build_configurations.map { |configuration|
  configuration.build_settings[key]
}, 'beforeEnvMasked' => before, 'environmentAfter' => ENV[key],
  'xcodeprojVersion' => Gem.loaded_specs.fetch('xcodeproj').version.to_s})
'''


def execute_hook(minimum, versions, *, environment_minimum=None, helper_minimum=None):
    ruby = shutil.which('ruby')
    if ruby is None:
        raise AssertionError('Ruby is required to execute the actual CocoaPods hook source')
    environment = {key: value for key, value in os.environ.items() if key not in ('RUBYOPT', 'RUBYLIB')}
    if environment_minimum is None:
        environment.pop('MACOSX_DEPLOYMENT_TARGET', None)
    else:
        environment['MACOSX_DEPLOYMENT_TARGET'] = environment_minimum
    data = json.dumps({'targets': versions, 'helperMinimum': helper_minimum})
    return subprocess.run([ruby, '-rjson', '-rrubygems/version', '-e', RUBY, data],
                          input=P.pod_post_install(minimum), env=environment,
                          text=True, capture_output=True, timeout=15, check=False)


def execute_xcodeproj_hook(minimum, **settings):
    """Use an existing installed Ruby/Xcodeproj; never install or fetch gems."""
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('RUBYOPT', 'RUBYLIB', 'MACOSX_DEPLOYMENT_TARGET')}
    environment['MACOSX_DEPLOYMENT_TARGET'] = minimum
    ruby = None
    for candidate in dict.fromkeys((shutil.which('ruby'), '/usr/bin/ruby')):
        if candidate is None or not Path(candidate).is_file():
            continue
        probe = subprocess.run([candidate, '-rxcodeproj', '-e',
                                'puts Gem.loaded_specs.fetch("xcodeproj").version'],
                               env=environment, text=True, capture_output=True, timeout=15, check=False)
        if probe.returncode == 0 and probe.stdout.strip() == '1.28.1':
            ruby = candidate
            break
    if ruby is None:
        raise AssertionError('An existing Ruby with Xcodeproj 1.28.1 is required for the real xcconfig regression')
    scratch = HERE.parent / '_scratch/pod-minimum-fix/xcodeproj-controls'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='source-', dir=scratch) as directory:
        data = json.dumps(dict(settings, directory=directory))
        return subprocess.run([ruby, '-rjson', '-rrubygems/version', '-e', XCODEPROJ_RUBY, data],
                              input=P.pod_post_install(minimum), env=environment,
                              text=True, capture_output=True, timeout=15, check=False)


class PodMinimumControls(unittest.TestCase):
    def test_complete_original_profile_roundtrip_idempotence_and_architecture_switch(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.encode()).hexdigest(), P.ORIGINAL_PODFILE_SHA256)
        for newline in ('\n', '\r\n'):
            original = ORIGINAL.replace('\n', newline)
            for minimum in ('10.14', '12.3'):
                configured = P.patch_pod_minimum(original, minimum)
                self.assertEqual(P.patch_pod_minimum(configured, minimum), configured)
                old = configured.replace(P.pod_post_install(minimum).replace('\n', newline),
                                         P.OLD_POD_POST_INSTALL.replace('\n', newline), 1)
                old = old.replace("platform :osx, '" + minimum + "'", "platform :osx, '10.14'", 1)
                self.assertEqual(old, original)
                other = '12.3' if minimum == '10.14' else '10.14'
                self.assertEqual(P.patch_pod_minimum(configured, other), P.patch_pod_minimum(original, other))
                previous = original.replace(P.OLD_POD_POST_INSTALL.replace('\n', newline),
                                            P.previous_pod_post_install(minimum).replace('\n', newline), 1)
                previous = previous.replace("platform :osx, '10.14'", "platform :osx, '" + minimum + "'", 1)
                self.assertEqual(P.patch_pod_minimum(previous, minimum), configured)

    def test_drift_duplicate_missing_moved_and_partial_hooks_refuse(self):
        current = P.patch_pod_minimum(ORIGINAL, '12.3')
        hook = P.pod_post_install('12.3')
        mutations = (current.replace(hook, '', 1), current.replace(hook, hook + hook, 1),
                     current.replace(hook, '', 1) + '# moved\n' + hook,
                     current.replace('versions.max', 'versions.min', 1),
                     current + '# unrelated drift\n', current.replace("'12.3'", "'12.0'", 1),
                     current.replace('\n', '\r\n', 1), current + '\r')
        for mutated in mutations:
            with self.subTest(mutated=mutated[-70:]), self.assertRaises(RuntimeError):
                P.patch_pod_minimum(mutated, '12.3')
        with self.assertRaises(RuntimeError):
            P.patch_pod_minimum(ORIGINAL, '12.0')

    def test_actual_arm_hook_clamps_every_configuration_and_preserves_higher(self):
        result = execute_hook('12.3', [['10.13', '10.14', '12.0', '12.3', '13.0', None], ['10.9', '15.0']],
                              environment_minimum='12.3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'versions': [
            ['12.3', '12.3', '12.3', '12.3', '13.0', '12.3'], ['12.3', '15.0']], 'flutterCalls': 2})

    def test_actual_intel_hook_retains_1014_advertised_minimum(self):
        result = execute_hook('10.14', [['10.13', '10.14', '12.3', None]], environment_minimum='10.14')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['versions'], [['10.14', '10.14', '12.3', '10.14']])
        self.assertIn("platform :osx, '10.14'", P.patch_pod_minimum(ORIGINAL, '10.14'))

    def test_actual_hook_follows_flutter_defaults_and_handles_unset_environment(self):
        result = execute_hook('12.3', [[None, '13.0']], helper_minimum='10.14')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'versions': [['12.3', '13.0']], 'flutterCalls': 1})

    def test_environment_disagreement_or_unresolved_pod_minimum_remains_fatal(self):
        for minimum, environment in (('12.3', '10.14'), ('10.14', '12.3'), ('12.3', 'not-a-version')):
            result = execute_hook(minimum, [['10.13']], environment_minimum=environment)
            self.assertNotEqual(result.returncode, 0)
        result = execute_hook('12.3', [['$(inherited)']], environment_minimum='12.3')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('inhibit_all_warnings', P.pod_post_install('12.3'))
        self.assertNotIn('GCC_WARN_INHIBIT_ALL_WARNINGS', P.pod_post_install('12.3'))

    def test_real_xcodeproj_preserves_higher_base_and_project_minima_despite_environment(self):
        for settings, expected in (({'base': '13.0'}, '13.0'),
                                   ({'projectBase': '14.0'}, '14.0'),
                                   ({'projectDirect': '15.0', 'base': '13.0', 'direct': '10.13'}, '15.0'),
                                   ({'projectBase': '13.0', 'direct': '$(inherited)'}, '13.0'),
                                   ({'base': '13.0', 'helperMinimum': '10.14'}, '13.0'),
                                   ({'base': '13.0', 'helperMinimum': '14.0'}, '14.0'),
                                   ({'includedBase': '13.0', 'base': '12.0'}, '13.0')):
            with self.subTest(settings=settings):
                result = execute_xcodeproj_hook('12.3', **settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {
                    'versions': [expected, expected], 'beforeEnvMasked': ['12.3', '12.3'],
                    'environmentAfter': '12.3', 'xcodeprojVersion': '1.28.1'})

    def test_real_xcodeproj_unresolved_base_or_inheritance_refuses_and_restores_environment(self):
        for settings in ({'base': '$(UNKNOWN_DEPLOYMENT_TARGET)'}, {'base': 'not-a-version'},
                         {'direct': '$(inherited)'}, {'base': '$(inherited)'},
                         {'includedBase': '$(inherited)'},
                         {'projectBase': '$(UNKNOWN_DEPLOYMENT_TARGET)'}):
            with self.subTest(settings=settings):
                result = execute_xcodeproj_hook('12.3', **settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unresolved', result.stderr.lower())
                self.assertEqual(json.loads(result.stdout), {'environmentAfter': '12.3'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
