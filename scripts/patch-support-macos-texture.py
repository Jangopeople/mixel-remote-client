#!/usr/bin/env python3
"""Pin an owned renderer dependency and retire Mac texture pointers safely."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time

PIN = "42797e0f03141dc2b585f76c64a13974508058b4"
URL = "https://github.com/rustdesk-org/flutter_texture_rgba_renderer.git"
TREE = "c92d9fd866c068977d245323e0828446b1fbf71b"
INVENTORY_SHA = "bbfe9564756aff1e8eb2f51d1b8f6fec31bef5956c36b62d1afceb28fdec7114"
PLUGIN = "macos/Classes/TextureRgbaRendererPlugin.swift"
PLUGIN_SHA = "6c802f3b20a7d92e4a2af729880701cbc4b98304e535875150f9a0d8b74ccd7a"
CALLBACK_START = '    #[cfg(not(any(target_os = "android", target_os = "ios")))]\n    pub fn on_rgba('
CALLBACK_END = '\n    #[cfg(feature = "vram")]\n    pub fn on_texture'
CALLBACK_SHA = "b51ea9c6a259684d0cea1ff5ab3798c143f7a5c76b02003b3c91efd7eda6bbe9"
DEPENDENCY = ".mixel-dependencies/texture_rgba_renderer"
OLD_DEP = """  texture_rgba_renderer:
    git:
      url: https://github.com/rustdesk-org/flutter_texture_rgba_renderer
      ref: 42797e0f03141dc2b585f76c64a13974508058b4"""
NEW_DEP = """  texture_rgba_renderer:
    # Owned copy of exact 42797e0; only Mac texture lifecycle and validated row copying differ.
    path: .mixel-dependencies/texture_rgba_renderer"""
OLD_CREATE = """            renderer[key] = textRgba
            result(textRgba.textureId)"""
NEW_CREATE = """            // Flutter's engine returns 0 when registration fails; no pointer
            // may be published for an unregistered texture.
            if textRgba.textureId > 0 {
                renderer[key] = textRgba
            }
            result(textRgba.textureId)"""
OLD_CLOSE = """        if (renderer[key] != nil) {
            if (-1 != renderer[key]?.textureId) {
                texture_registry?.unregisterTexture(renderer[key]?.textureId ?? -1)
            }
            result(true)
        } else {
            result(false)
        }
        
    case \"onRgba\":"""
NEW_CLOSE = """        if let texture = renderer.removeValue(forKey: key) {
            // The raw-pointer retirement precedes close; keep this owner alive
            // through unregister even if the registry uses weak references.
            withExtendedLifetime(texture) {
                if texture.textureId > 0 {
                    texture_registry?.unregisterTexture(texture.textureId)
                }
            }
            result(true)
        } else {
            result(false)
        }
        
    case \"onRgba\":"""
TEXT = "macos/Classes/TextRgba.swift"
TEXT_SHA = "18c831c3510cdb0862abb05b22a89e5ded1edbdeb253ff38b8535c96e01d0a68"
OLD_FRAME = """    private func _markFrameAvaliable(buffer: UnsafePointer<UInt8>, len: Int, width: Int, height: Int, stride_align: Int) -> Bool {
        var pixelBufferCopy: CVPixelBuffer?
        // macOS only support 32BGRA currently.
        let dict: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferMetalCompatibilityKey as String: true,
            kCVPixelBufferOpenGLCompatibilityKey as String: true,
            // https://developer.apple.com/forums/thread/712709
            kCVPixelBufferBytesPerRowAlignmentKey as String: 64
        ]
        let result = CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA, dict as CFDictionary, &pixelBufferCopy)
        guard result == kCVReturnSuccess else {
            return false
        }

        CVPixelBufferLockBaseAddress(pixelBufferCopy!, [])
        let ptr = CVPixelBufferGetBaseAddress(pixelBufferCopy!)!
        memcpy(ptr, buffer, len)
        CVPixelBufferUnlockBaseAddress(pixelBufferCopy!, [])
        self.data = pixelBufferCopy
        self.width = width
        self.height = height
        if textureId != -1 && self.data != nil {
            registry?.textureFrameAvailable(textureId)
            return true
        } else {
            return false
        }
    }
"""
NEW_FRAME = """    private func _markFrameAvaliable(buffer: UnsafePointer<UInt8>, len: Int, width: Int, height: Int, stride_align: Int) -> Bool {
        // Multi-display rendering also uses this path with packed (align1)
        // source rows. Validate lengths before allocating or reading bytes.
        guard width > 0, height > 0, stride_align > 0,
              (stride_align & (stride_align - 1)) == 0 else { return false }
        let (rowBytes, rowOverflow) = width.multipliedReportingOverflow(by: 4)
        let (alignedBytes, alignOverflow) = rowBytes.addingReportingOverflow(stride_align - 1)
        guard !rowOverflow, !alignOverflow else { return false }
        let sourceStride = alignedBytes & ~(stride_align - 1)
        let (sourceSize, sourceOverflow) = sourceStride.multipliedReportingOverflow(by: height)
        guard !sourceOverflow, len == sourceSize else { return false }

        var pixelBufferCopy: CVPixelBuffer?
        let dict: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferMetalCompatibilityKey as String: true,
            kCVPixelBufferOpenGLCompatibilityKey as String: true,
            kCVPixelBufferBytesPerRowAlignmentKey as String: 64
        ]
        let result = CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA, dict as CFDictionary, &pixelBufferCopy)
        guard result == kCVReturnSuccess, let pixelBuffer = pixelBufferCopy else {
            return false
        }
        guard CVPixelBufferLockBaseAddress(pixelBuffer, []) == kCVReturnSuccess else {
            return false
        }
        let destinationStride = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let (destinationSize, destinationOverflow) = destinationStride.multipliedReportingOverflow(by: height)
        guard !destinationOverflow, destinationStride >= rowBytes,
              CVPixelBufferGetDataSize(pixelBuffer) >= destinationSize,
              let destination = CVPixelBufferGetBaseAddress(pixelBuffer) else {
            CVPixelBufferUnlockBaseAddress(pixelBuffer, [])
            return false
        }
        for row in 0..<height {
            memcpy(destination.advanced(by: row * destinationStride),
                   buffer.advanced(by: row * sourceStride), rowBytes)
            // Do not expose allocator contents through destination row padding.
            memset(destination.advanced(by: row * destinationStride + rowBytes),
                   0, destinationStride - rowBytes)
        }
        guard CVPixelBufferUnlockBaseAddress(pixelBuffer, []) == kCVReturnSuccess else {
            return false
        }
        // Preserve the last valid frame on every rejected/copy-failed input.
        self.data = pixelBuffer
        self.width = width
        self.height = height
        if textureId > 0 {
            registry?.textureFrameAvailable(textureId)
            return true
        } else {
            return false
        }
    }
"""
OLD_DATA = """    @objc public func markFrameAvaliable(data: Data, width: Int, height: Int, stride_align: Int) -> Bool {
        data.withUnsafeBytes { buffer in
            markFrameAvaliableRaw(
                buffer: buffer.baseAddress!.assumingMemoryBound(to: UInt8.self),
                len: data.count,
                width: width,
                height: height,
                stride_align: stride_align)
        }
    }"""
NEW_DATA = """    @objc public func markFrameAvaliable(data: Data, width: Int, height: Int, stride_align: Int) -> Bool {
        data.withUnsafeBytes { buffer in
            guard let baseAddress = buffer.baseAddress else { return false }
            return markFrameAvaliableRaw(
                buffer: baseAddress.assumingMemoryBound(to: UInt8.self),
                len: data.count,
                width: width,
                height: height,
                stride_align: stride_align)
        }
    }"""

METHOD = """
    #[cfg(target_os = \"macos\")]
    fn unregister_pixelbuffer_texture_if_current(&self, display: usize, ptr: usize) -> bool {
        let mut sessions_lock = self.map_display_sessions.write().unwrap();
        let Some(info) = sessions_lock.get_mut(&display) else {
            return false;
        };
        if ptr == 0 || info.texture_rgba_ptr != ptr as TextureRgbaPtr {
            return false;
        }
        // This lock also surrounds the native frame callback. After it returns,
        // no callback can still use the retired pointer; a newer pointer is kept.
        info.texture_rgba_ptr = usize::default();
        #[cfg(feature = \"vram\")]
        if info.gpu_output_ptr != usize::default() {
            return true;
        }
        sessions_lock.remove(&display);
        true
    }
"""
GLOBAL = """
#[inline]
pub fn session_unregister_pixelbuffer_texture_if_current(
    session_id: SessionID, display: usize, ptr: usize,
) -> bool {
    #[cfg(target_os = \"macos\")]
    for s in sessions::get_sessions() {
        if let Some(h) = s.ui_handler.session_handlers.read().unwrap().get(&session_id) {
            return h.renderer.unregister_pixelbuffer_texture_if_current(display, ptr);
        }
    }
    #[cfg(not(target_os = \"macos\"))]
    let _ = (session_id, display, ptr);
    false
}
"""
FFI = """
pub fn session_unregister_pixelbuffer_texture_if_current(
    session_id: SessionID, display: usize, ptr: usize,
) -> SyncReturn<bool> {
    SyncReturn(super::flutter::session_unregister_pixelbuffer_texture_if_current(
        session_id, display, ptr,
    ))
}
"""
WEB = """
  bool sessionUnregisterPixelbufferTextureIfCurrent(
          {required UuidValue sessionId, required int display, required int ptr}) =>
      false;
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch_plugin(text):
    original = text.replace(NEW_CLOSE, OLD_CLOSE) if text.count(NEW_CLOSE) == 1 else text
    original = original.replace(NEW_CREATE, OLD_CREATE) if original.count(NEW_CREATE) == 1 else original
    if sha(original.encode()) != PLUGIN_SHA or original.count(OLD_CLOSE) != 1 or original.count(OLD_CREATE) != 1:
        raise RuntimeError("Pinned renderer close source changed")
    return original.replace(OLD_CLOSE, NEW_CLOSE, 1).replace(OLD_CREATE, NEW_CREATE, 1)


def unpatch_plugin(text):
    patched = patch_plugin(text)
    return patched.replace(NEW_CLOSE, OLD_CLOSE, 1).replace(NEW_CREATE, OLD_CREATE, 1)


def patch_frame(text):
    original = text
    for old, new in ((OLD_FRAME, NEW_FRAME), (OLD_DATA, NEW_DATA)):
        if original.count(new) == 1:
            original = original.replace(new, old, 1)
    if sha(original.encode()) != TEXT_SHA:
        raise RuntimeError("Pinned renderer frame source changed")
    for old, new in ((OLD_FRAME, NEW_FRAME), (OLD_DATA, NEW_DATA)):
        if original.count(old) != 1:
            raise RuntimeError("Pinned renderer frame boundary changed")
        original = original.replace(old, new, 1)
    return original


def unpatch_frame(text):
    return patch_frame(text).replace(NEW_FRAME, OLD_FRAME, 1).replace(NEW_DATA, OLD_DATA, 1)


def insert(text, start, end, digest, addition):
    if text.count(addition) > 1:
        raise RuntimeError("Duplicate texture lifecycle patch")
    original = text.replace(addition, "", 1) if addition in text else text
    if original.count(start) != 1:
        raise RuntimeError("Pinned texture boundary changed")
    a = original.index(start)
    b = original.index(end, a)
    if sha(original[a:b].encode()) != digest:
        raise RuntimeError("Pinned texture registration implementation changed")
    return original[:b] + addition + original[b:]


def patch_sources(repo):
    for name in ("src/flutter.rs", "src/flutter_ffi.rs", "flutter/lib/web/bridge.dart",
                 "flutter/lib/models/desktop_render_texture.dart", "flutter/pubspec.yaml"):
        path = repo / name
        if not path.is_file() or path.stat().st_nlink != 1:
            raise RuntimeError("Nonregular texture source")
        for ancestor in (path, *path.parents):
            if ancestor == repo.parent:
                break
            if ancestor.is_symlink():
                raise RuntimeError("Symlink in texture source path")
    texts = {f: (repo / f).read_text(encoding="utf-8") for f in (
        "src/flutter.rs", "src/flutter_ffi.rs", "flutter/lib/web/bridge.dart",
        "flutter/lib/models/desktop_render_texture.dart", "flutter/pubspec.yaml")}
    f = "src/flutter.rs"
    a = texts[f].index(CALLBACK_START)
    b = texts[f].index(CALLBACK_END, a)
    if sha(texts[f][a:b].encode()) != CALLBACK_SHA:
        raise RuntimeError("Pinned texture frame callback lock changed")
    texts[f] = insert(texts[f], "    fn register_pixelbuffer_texture(",
                      '\n    #[cfg(feature = "vram")]\n    pub fn register_gpu_output',
                      "8662dc2d83d365c2cfad98c29b41da39f1e5b0bf1586573f29720487d77a0934", METHOD)
    texts[f] = insert(texts[f], "pub fn session_register_pixelbuffer_texture(",
                      "\n#[inline]\npub fn session_register_gpu_texture",
                      "a751739e485651dc84372a456f96285868341b5d82886e12dc92aeb2487be2ec", GLOBAL)
    f = "src/flutter_ffi.rs"
    texts[f] = insert(texts[f], "pub fn session_register_pixelbuffer_texture(",
                      "\npub fn session_register_gpu_texture",
                      "2c75b9d833e184796df19a4e754c6b47b78780821ed9125b09c12de88c839ba5", FFI)
    f = "flutter/lib/web/bridge.dart"
    texts[f] = insert(texts[f], "  void sessionRegisterPixelbufferTexture(",
                      "\n  void sessionRegisterGpuTexture",
                      "e22d2ab261c54ec0510ff356a41438a984624d029f70307b67903847db5f123f", WEB)
    f = "flutter/lib/models/desktop_render_texture.dart"
    text = texts[f]
    a = text.index("class _PixelbufferTexture {")
    b = text.index("\nclass _GpuTexture {", a)
    block = text[a:b]
    replacements = (
        ("  bool _destroying = false;", "  bool _destroying = false;\n  bool _closed = false;\n  int _texturePtr = 0;\n  final Set<int> _retiringKeys = {};"),
        ("    _display = d;", "    _display = d;\n    _closed = false;"),
        ("    textureRenderer.createTexture(_textureKey).then((id) async {", "    final textureKey = _textureKey;\n    textureRenderer.createTexture(textureKey).then((id) async {\n      if (isMacOS && (_closed || _textureKey != textureKey)) {\n        // Creation may finish after disposal; close that exact generation too.\n        await textureRenderer.closeTexture(textureKey);\n        return;\n      }"),
        ("      _id = id;", "      if (isMacOS && id <= 0) {\n        await textureRenderer.closeTexture(textureKey);\n        return;\n      }\n      _id = id;"),
        ("        final ptr = await textureRenderer.getTexturePtr(_textureKey);", "        final ptr = await textureRenderer.getTexturePtr(textureKey);\n        if (isMacOS && (_closed || _textureKey != textureKey || ptr == 0)) {\n          await textureRenderer.closeTexture(textureKey);\n          return;\n        }\n        _texturePtr = ptr;"),
        ("  destroy(bool unregisterTexture, FFI ffi) async {", """  destroy(bool unregisterTexture, FFI ffi) async {
    if (isMacOS) {
      final textureKey = _textureKey;
      final sessionId = _sessionId;
      if (textureKey == -1 || sessionId == null ||
          !_retiringKeys.add(textureKey)) return;
      _closed = true;
      final texturePtr = _texturePtr;
      // Retire only this pointer under the native frame lock, even during
      // tab/window handoff. A replacement for the same session must survive.
      try {
        bind.sessionUnregisterPixelbufferTextureIfCurrent(
            sessionId: sessionId, display: display, ptr: texturePtr);
      } catch (_) {
        // Unknown native retirement must not free a potentially live pointer.
        _retiringKeys.remove(textureKey);
        rethrow;
      }
      _texturePtr = 0;
      try {
        await textureRenderer.closeTexture(textureKey);
      } finally {
        _retiringKeys.remove(textureKey);
        if (_textureKey == textureKey) _textureKey = -1;
      }
      return;
    }"""),
    )
    original = block
    for old, new in replacements:
        if original.count(new) == 1:
            original = original.replace(new, old, 1)
    if sha(original.encode()) != "414f784bc5a1b1a3bfb410d7c438142790777096431ed0bf3c1d6ae6c960c19e":
        raise RuntimeError("Pinned pixel texture Dart lifetime changed")
    for old, new in replacements:
        if original.count(old) != 1:
            raise RuntimeError("Pinned pixel texture replacement ambiguous")
        original = original.replace(old, new, 1)
    texts[f] = text[:a] + original + text[b:]
    f = "flutter/pubspec.yaml"
    original = texts[f].replace(NEW_DEP, OLD_DEP) if texts[f].count(NEW_DEP) == 1 else texts[f]
    if original.count(OLD_DEP) != 1 or original.count("  texture_rgba_renderer:") != 1:
        raise RuntimeError("Pinned texture dependency changed")
    texts[f] = original.replace(OLD_DEP, NEW_DEP, 1)
    return texts


def source_files(archive):
    if len(archive) > 2_000_000:
        raise RuntimeError("Pinned source archive exceeds bound")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for entry in tar.getmembers():
            name = entry.name
            path = PurePosixPath(name)
            if "\0" in name or path.is_absolute() or any(p in (".", "..", "") for p in path.parts) or str(path) != name.rstrip("/"):
                raise RuntimeError("Noncanonical pinned source member")
            if entry.isdir():
                continue
            if not entry.isfile() or entry.size > 1_000_000 or name in files:
                raise RuntimeError("Unexpected pinned source member")
            files[name] = tar.extractfile(entry).read()
    inventory = {name: sha(data) for name, data in files.items()}
    if len(files) != 98 or sha(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()) != INVENTORY_SHA:
        raise RuntimeError("Complete pinned renderer inventory changed")
    return files


def fetch_source():
    for attempt in range(3):
        try:
            with tempfile.TemporaryDirectory(prefix="mixel-texture-fetch-") as directory:
                subprocess.run(["git", "init", "-q", directory], check=True, capture_output=True, timeout=30)
                subprocess.run(["git", "-C", directory, "fetch", "-q", "--depth=1", URL, PIN], check=True, capture_output=True, timeout=90)
                head = subprocess.check_output(["git", "-C", directory, "rev-parse", "FETCH_HEAD"], timeout=30).decode().strip()
                tree = subprocess.check_output(["git", "-C", directory, "rev-parse", "FETCH_HEAD^{tree}"], timeout=30).decode().strip()
                if head != PIN or tree != TREE:
                    raise RuntimeError("Fetched renderer identity changed")
                return source_files(subprocess.check_output(["git", "-C", directory, "archive", "--format=tar", PIN], timeout=30))
        except (subprocess.SubprocessError, OSError):
            if attempt == 2:
                raise RuntimeError("Pinned renderer fetch failed after 3 attempts") from None
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def existing_files(target):
    if target.is_symlink() or not target.is_dir():
        raise RuntimeError("Owned renderer path is not a regular directory")
    files = {}
    for path in target.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Symlink in owned renderer")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 1_000_000:
            raise RuntimeError("Nonregular owned renderer member")
        files[path.relative_to(target).as_posix()] = path.read_bytes()
    try:
        proof = json.loads(files.pop(".mixel-source.json"))
    except (KeyError, ValueError):
        raise RuntimeError("Missing owned renderer provenance") from None
    original = dict(files)
    original[PLUGIN] = unpatch_plugin(original[PLUGIN].decode()).encode()
    original[TEXT] = unpatch_frame(original[TEXT].decode()).encode()
    inventory = {name: sha(data) for name, data in original.items()}
    expected = {"commit": PIN, "tree": TREE, "original_inventory_sha256": INVENTORY_SHA}
    if proof != expected or len(files) != 98 or sha(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()) != INVENTORY_SHA:
        raise RuntimeError("Owned renderer content or provenance changed")
    expected_dirs = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents if str(parent) != "."}
    actual_dirs = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_dir()}
    if actual_dirs != expected_dirs:
        raise RuntimeError("Unexpected owned renderer directory")
    if files[TEXT].decode() != patch_frame(files[TEXT].decode()):
        raise RuntimeError("Owned renderer frame patch missing")
    if files[PLUGIN].decode() != patch_plugin(files[PLUGIN].decode()):
        raise RuntimeError("Owned renderer patch missing")
    return files


def apply(repo):
    cargo = (repo / "Cargo.toml").read_text()
    package = re.search(r"(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)", cargo)
    if not package or re.findall(r'^version\s*=\s*"([^"]+)"\s*$', package[1], re.M) != ["1.4.6"]:
        raise RuntimeError("Mac texture correction requires upstream 1.4.6")
    texts = patch_sources(repo)  # Reject every source drift before fetching/writing.
    parent = repo / "flutter/.mixel-dependencies"
    for path in (repo / "flutter", parent):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError("Owned renderer parent is not a regular directory")
    target = repo / "flutter" / DEPENDENCY
    if target.exists() or target.is_symlink():
        existing_files(target)
    else:
        files = fetch_source()
        files[PLUGIN] = patch_plugin(files[PLUGIN].decode()).encode()
        files[TEXT] = patch_frame(files[TEXT].decode()).encode()
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".mixel-texture-stage-", dir=parent) as directory:
            stage = Path(directory) / "texture_rgba_renderer"
            stage.mkdir()
            for name, data in files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (stage / ".mixel-source.json").write_text(json.dumps({"commit": PIN, "tree": TREE, "original_inventory_sha256": INVENTORY_SHA}, sort_keys=True) + "\n")
            existing_files(stage)
            if target.exists() or target.is_symlink():
                raise RuntimeError("Owned renderer destination appeared during preparation")
            stage.rename(target)
    for name, text in texts.items():
        with (repo / name).open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print("   patched pinned Mac texture close: retire matching native pointer and release closed renderer; owned dependency42797e, validated row copies; shared cache untouched")


if __name__ == "__main__":
    apply(Path(os.environ.get("RDREPO", "./rustdesk")).resolve())
