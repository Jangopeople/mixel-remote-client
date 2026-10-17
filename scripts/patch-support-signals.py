#!/usr/bin/env python3
"""Give the embedded Linux runner Rust's normal broken-pipe semantics."""
import os
from pathlib import Path


INCLUDE = '#include <dlfcn.h>\n'
PATCHED_INCLUDE = INCLUDE + '#include <csignal>\n#include <cstdio>\n#include <cstdlib>\n'
ENTRY = 'int main(int argc, char** argv) {\n'
PATCHED_ENTRY = ENTRY + '''  // The Flutter runner embeds Rust through dlopen, bypassing Rust executable
  // startup. A broken native socket or pipe must return EPIPE, not terminate
  // the incoming service and its IPC listener after a network interruption.
  if (std::signal(SIGPIPE, SIG_IGN) == SIG_ERR) {
    std::perror("Mixel Remote: failed to initialize broken-pipe handling");
    return EXIT_FAILURE;
  }
'''


def replace_once(source: str, original: str, patched: str) -> str:
    if patched in source:
        if source.count(patched) != 1:
            raise RuntimeError("Pinned upstream Linux runner signal initialization changed")
        return source
    if source.count(original) != 1:
        raise RuntimeError("Pinned upstream Linux runner signal initialization changed")
    return source.replace(original, patched, 1)


def patch(source: str) -> str:
    if source.count(ENTRY) != 1 or (
            ('std::signal(' in source or 'broken-pipe handling' in source)
            and PATCHED_ENTRY not in source):
        raise RuntimeError("Pinned upstream Linux runner signal initialization changed")
    source = replace_once(source, INCLUDE, PATCHED_INCLUDE)
    source = replace_once(source, ENTRY, PATCHED_ENTRY)
    # Installation must precede dlopen, native constructors and any threads.
    if source.count('std::signal(SIGPIPE, SIG_IGN)') != 1:
        raise RuntimeError("Pinned upstream Linux runner signal initialization changed")
    if source.index(PATCHED_ENTRY) > source.index('if (!flutter_rustdesk_core_main())'):
        raise RuntimeError("Linux runner signal initialization is not first")
    return source


if __name__ == '__main__':
    path = Path(os.environ.get('RDREPO', './rustdesk')) / 'flutter/linux/main.cc'
    path.write_text(patch(path.read_text(encoding='utf-8')), encoding='utf-8')
    print('   patched Linux native runner SIGPIPE handling before Rust library loading')
