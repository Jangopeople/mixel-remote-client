#!/usr/bin/env python3
"""Keep unpublished Windows diagnostic continuation separate from release success."""
import argparse
import os
from pathlib import Path


def require(value, message):
    if not value:
        raise ValueError(message)


def validate_request(environment):
    enabled = environment.get('COLLECT_WINDOWS_PREFLIGHT_FAILURE', '')
    require(enabled in ('', 'false', 'true'), 'Invalid Windows preflight diagnostic request')
    if enabled != 'true':
        return False
    require(environment.get('GITHUB_EVENT_NAME') == 'workflow_dispatch',
            'Windows preflight diagnostics require workflow_dispatch')
    require(environment.get('PUBLISH_REQUESTED') == 'false',
            'Windows preflight diagnostics require publish_r2=false')
    require(environment.get('INPUT_ONLY') == 'windows',
            'Windows preflight diagnostics require only=windows exactly')
    require(environment.get('WINDOWS_RELEASE_GATE') == 'false',
            'Windows preflight diagnostics cannot request the artifact release gate')
    require(all(environment.get(name, '') == '' for name in
                ('RUNTIME_RUN_ID', 'RUNTIME_LINUX_RUN', 'RUNTIME_MACOS_RUN')),
            'Windows preflight diagnostics cannot request artifact runtimes')
    return True


def validate_outcome(environment):
    validate_request(environment)
    require(environment.get('SOURCE_PREFLIGHT_OUTCOME') == 'success',
            'Original Windows generated-source preflight did not succeed; release remains blocked')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('request', 'outcome'))
    args = parser.parse_args()
    try:
        if args.command == 'request':
            enabled = validate_request(os.environ)
            output = os.environ.get('GITHUB_OUTPUT')
            if output:
                with Path(output).open('a', encoding='utf-8') as stream:
                    stream.write('collect_windows_preflight_failure=' + str(enabled).lower() + '\n')
            print('PASS: unpublished Windows preflight diagnostic request validated.' if enabled else
                  'PASS: Windows preflight diagnostic continuation disabled.')
        else:
            validate_outcome(os.environ)
            print('PASS: original Windows generated-source preflight succeeded; customer artifact gate retained.')
    except ValueError as error:
        parser.exit(1, 'ERROR: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
