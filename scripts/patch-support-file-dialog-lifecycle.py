#!/usr/bin/env python3
"""Stop the pinned file-dialog event loop permanently when its owner closes."""
import argparse
import hashlib
import os
from pathlib import Path
import stat

SOURCE = "flutter/lib/utils/event_loop.dart"
ORIGINAL_SHA = "9fc1b26b90cefdfc491c4dfdb3f8fb14ad4e6fbc84d31ab2cfe2981b50a5a8a9"
MODEL = "flutter/lib/models/file_model.dart"
MODEL_SHA = "34789849de62ee6d8eab9cb5b30ea1d9f71fce0cfb903caf9d8620ee42cd0c24"
MODEL_REPLACEMENTS = (
    ("  late final FileDialogEventLoop evtLoop;", "  late FileDialogEventLoop evtLoop;"),
    ("  Future<void> onReady() async {\n    await evtLoop.onReady();",
     "  Future<void> onReady() async {\n"
     "    // Mobile sessions reuse this FileModel; give each session a new loop\n"
     "    // without reopening callbacks belonging to the closed session.\n"
     "    if (evtLoop.isClosed) evtLoop = FileDialogEventLoop();\n"
     "    await evtLoop.onReady();"),
)
OLD = """abstract class BaseEventLoop<EventType, Data> {
  final List<BaseEvent<EventType, Data>> _evts = [];
  Timer? _timer;

  List<BaseEvent<EventType, Data>> get evts => _evts;

  Future<void> onReady() async {
    // Poll every 100ms.
    _timer = Timer.periodic(Duration(milliseconds: 100), _handleTimer);
  }

  /// An Event is about to be consumed.
  Future<void> onPreConsume(BaseEvent<EventType, Data> evt) async {}
  /// An Event was consumed.
  Future<void> onPostConsume(BaseEvent<EventType, Data> evt) async {}
  /// Events are all handled and cleared.
  Future<void> onEventsClear() async {}
  /// Events start to consume.
  Future<void> onEventsStartConsuming() async {}

  Future<void> _handleTimer(Timer timer) async {
      if (_evts.isEmpty) {
        return;
      }
      timer.cancel();
      _timer = null;
      // Handle the logic.
      await onEventsStartConsuming();
      while (_evts.isNotEmpty) {
        final evt = _evts.first;
        _evts.remove(evt);
        await onPreConsume(evt);
        await evt.consume();
        await onPostConsume(evt);
      }
      await onEventsClear();
      // Now events are all processed.
      _timer = Timer.periodic(Duration(milliseconds: 100), _handleTimer);
  }

  Future<void> close() async {
    _timer?.cancel();
  }

  void pushEvent(BaseEvent<EventType, Data> evt) {
    _evts.add(evt);
  }

  void clear() {
    _evts.clear();
  }
}
"""
NEW = """abstract class BaseEventLoop<EventType, Data> {
  final List<BaseEvent<EventType, Data>> _evts = [];
  Timer? _timer;
  bool _closed = false;
  bool _started = false;

  List<BaseEvent<EventType, Data>> get evts => _evts;
  bool get isClosed => _closed;

  Future<void> onReady() async {
    if (_closed || _started) return;
    _started = true;
    // Poll every 100ms.
    _timer = Timer.periodic(Duration(milliseconds: 100), _handleTimer);
  }

  /// An Event is about to be consumed.
  Future<void> onPreConsume(BaseEvent<EventType, Data> evt) async {}
  /// An Event was consumed.
  Future<void> onPostConsume(BaseEvent<EventType, Data> evt) async {}
  /// Events are all handled and cleared.
  Future<void> onEventsClear() async {}
  /// Events start to consume.
  Future<void> onEventsStartConsuming() async {}

  Future<void> _handleTimer(Timer timer) async {
      if (_closed || _evts.isEmpty) {
        return;
      }
      timer.cancel();
      _timer = null;
      // A callback already awaiting a dialog may finish after close. Do not
      // start another callback or recreate the timer when it resumes.
      await onEventsStartConsuming();
      if (_closed) return;
      while (!_closed && _evts.isNotEmpty) {
        final evt = _evts.first;
        _evts.remove(evt);
        await onPreConsume(evt);
        if (_closed) return;
        await evt.consume();
        if (_closed) return;
        await onPostConsume(evt);
        if (_closed) return;
      }
      await onEventsClear();
      // Now events are all processed, unless their owner closed while waiting.
      if (!_closed) {
        _timer = Timer.periodic(Duration(milliseconds: 100), _handleTimer);
      }
  }

  Future<void> close() async {
    _closed = true;
    _timer?.cancel();
    _timer = null;
    _evts.clear();
  }

  void pushEvent(BaseEvent<EventType, Data> evt) {
    if (_closed) return;
    _evts.add(evt);
  }

  void clear() {
    _evts.clear();
  }
}
"""

OVERRIDE_OLD = """  Future<void> overrideFileConfirm(Map<String, dynamic> evt,
      {bool? overrideConfirm, bool skip = false}) async {
    // If `skip == true`, it means to skip this file without showing dialog.
    // Because `resp` may be null after the user operation or the last remembered operation,
    // and we should distinguish them.
    final resp = overrideConfirm ??
        (!skip
            ? await showFileConfirmDialog(translate("Overwrite"),
                "${evt['read_path']}", true, evt['is_identical'] == "true")
            : null);
    final id = int.tryParse(evt['id']) ?? 0;
    if (false == resp) {
      final jobIndex = jobController.getJob(id);
      if (jobIndex != -1) {
        await jobController.cancelJob(id);
        final job = jobController.jobTable[jobIndex];
        job.state = JobState.done;
        jobController.jobTable.refresh();
      }
    } else {
      var need_override = false;
      if (resp == null) {
        // skip
        need_override = false;
      } else {
        // overwrite
        need_override = true;
      }
      // Update the loop config.
      if (fileConfirmCheckboxRemember) {
        evtLoop.setSkip(!need_override);
      }
      await bind.sessionSetConfirmOverrideFile(
          sessionId: sessionId,
          actId: id,
          fileNum: int.parse(evt['file_num']),
          needOverride: need_override,
          remember: fileConfirmCheckboxRemember,
          isUpload: evt['is_upload'] == "true");
    }
    // Update the loop config.
    if (fileConfirmCheckboxRemember) {
      evtLoop.setOverrideConfirm(resp);
    }
  }
"""
OVERRIDE_NEW = """  Future<void> overrideFileConfirm(Map<String, dynamic> evt,
      {bool? overrideConfirm, bool skip = false}) async {
    final ownerLoop = evtLoop;
    final ownerSessionId = sessionId;
    bool isCurrentOwner() => !ownerLoop.isClosed &&
        identical(evtLoop, ownerLoop) && sessionId == ownerSessionId;
    if (!isCurrentOwner()) return;
    // If `skip == true`, it means to skip this file without showing dialog.
    // Because `resp` may be null after the user operation or the last remembered operation,
    // and we should distinguish them.
    final resp = overrideConfirm ??
        (!skip
            ? await showFileConfirmDialog(translate("Overwrite"),
                "${evt['read_path']}", true, evt['is_identical'] == "true")
            : null);
    if (!isCurrentOwner()) return;
    final remember = fileConfirmCheckboxRemember;
    final id = int.tryParse(evt['id']) ?? 0;
    if (false == resp) {
      final jobIndex = jobController.getJob(id);
      if (jobIndex != -1) {
        await jobController.cancelJob(id);
        if (!isCurrentOwner()) return;
        final currentIndex = jobController.getJob(id);
        if (currentIndex != -1) {
          final job = jobController.jobTable[currentIndex];
          job.state = JobState.done;
          jobController.jobTable.refresh();
        }
      }
    } else {
      var need_override = false;
      if (resp == null) {
        // skip
        need_override = false;
      } else {
        // overwrite
        need_override = true;
      }
      // Update only this session's loop, even if an awaited call outlives it.
      if (remember) {
        ownerLoop.setSkip(!need_override);
      }
      await bind.sessionSetConfirmOverrideFile(
          sessionId: ownerSessionId,
          actId: id,
          fileNum: int.parse(evt['file_num']),
          needOverride: need_override,
          remember: remember,
          isUpload: evt['is_upload'] == "true");
      if (!isCurrentOwner()) return;
    }
    // Update the loop config.
    if (remember) {
      ownerLoop.setOverrideConfirm(resp);
    }
  }
"""
MODEL_REPLACEMENTS += ((OVERRIDE_OLD, OVERRIDE_NEW),)


def transform(data: bytes, restore: bool = False, *, name: str = SOURCE) -> bytes:
    text = data.decode("utf-8")
    logical = text.replace("\r\n", "\n")
    if "\r" in logical or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
        raise RuntimeError("Mixed file-dialog lifecycle source newlines")
    if name not in (SOURCE, MODEL):
        raise RuntimeError("Unknown file-dialog lifecycle source")
    pairs = ((OLD, NEW),) if name == SOURCE else MODEL_REPLACEMENTS
    original = logical
    for before, after in reversed(pairs):
        if after in original:
            if original.count(after) != 1 or before in original:
                raise RuntimeError("Mixed file-dialog lifecycle source: " + name)
            original = original.replace(after, before, 1)
    expected = ORIGINAL_SHA if name == SOURCE else MODEL_SHA
    if hashlib.sha256(original.encode()).hexdigest() != expected or any(original.count(before) != 1 for before, _ in pairs):
        raise RuntimeError("Pinned file-dialog lifecycle source changed: " + name)
    patched = original
    for before, after in pairs:
        patched = patched.replace(before, after, 1)
    if logical not in (original, patched):
        raise RuntimeError("Partial file-dialog lifecycle source: " + name)
    result = original if restore else patched
    return result.replace("\n", "\r\n" if "\r\n" in text else "\n").encode()


def apply(repo: Path, restore: bool = False, check: bool = False) -> None:
    repo = repo.absolute()
    changes = []
    states = []
    # Validate both complete sources and every path before the first write.
    for name in (SOURCE, MODEL):
        target = repo / name
        for path in (*reversed(target.parents), target):
            record = path.lstat()
            if stat.S_ISLNK(record.st_mode) or getattr(record, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("File-dialog lifecycle source ownership changed")
            if path == target:
                if not stat.S_ISREG(record.st_mode) or record.st_nlink != 1 or record.st_size > 128000:
                    raise RuntimeError("File-dialog lifecycle requires a single-link regular source")
            elif not stat.S_ISDIR(record.st_mode):
                raise RuntimeError("File-dialog lifecycle source parent is not a directory")
        data = target.read_bytes()
        original = transform(data, True, name=name)
        corrected = transform(data, restore, name=name)
        states.append(data == original)
        changes.append((target, data, corrected))
    if len(set(states)) != 1:
        raise RuntimeError("Partial file-dialog lifecycle source pair")
    if not check:
        for target, data, corrected in changes:
            if corrected != data:
                target.write_bytes(corrected)
    print("File-dialog lifecycle verified: terminal close and fresh loop for each reused session")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("RDREPO", "./rustdesk")))
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    apply(args.root, args.restore, args.check)
