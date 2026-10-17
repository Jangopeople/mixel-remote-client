#!/usr/bin/env bash
# Source this after Git Bash initializes PATH. Git's /usr/bin/link.exe is not
# the MSVC linker, even when VsDevCmd was imported by the previous native step.
if [[ "${OS:-}" != Windows_NT || "${VSCMD_ARG_TGT_ARCH:-}" != x64 || "${VSCMD_ARG_HOST_ARCH:-}" != x64 || -z "${VCToolsInstallDir:-}" ]]; then
  echo 'Native Git Bash builds require the imported x64 MSVC environment.' >&2
  return 1
fi
mixel_msvc_root="$(cygpath -u "$VCToolsInstallDir")" || return 1
mixel_msvc_bin="${mixel_msvc_root%/}/bin/Hostx64/x64"
if [[ ! -f "$mixel_msvc_bin/link.exe" || ! -f "$mixel_msvc_bin/cl.exe" ]]; then
  echo 'Imported native MSVC compiler/linker directory is absent.' >&2
  return 1
fi
export PATH="$mixel_msvc_bin:$PATH"
hash -r
if [[ "$(command -v link.exe)" != "$mixel_msvc_bin/link.exe" || "$(command -v cl.exe)" != "$mixel_msvc_bin/cl.exe" ]]; then
  echo 'Git Bash did not resolve the exact installed MSVC compiler and linker.' >&2
  return 1
fi
echo 'PASS: Git Bash resolves the exact installed x64 MSVC compiler and linker after shell initialization.'
unset mixel_msvc_root mixel_msvc_bin
