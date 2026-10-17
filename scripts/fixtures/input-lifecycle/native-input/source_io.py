"""Strict canonical source loading for the portable component gate."""
from pathlib import Path
import hashlib
import stat


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical_source(raw):
    """Accept either uniform checkout newline profile; preserve raw witnesses."""
    crlf = raw.count(b'\r\n')
    without = raw.replace(b'\r\n', b'')
    if b'\r' in without or (crlf and b'\n' in without):
        raise ValueError('mixed or unsupported production source newlines')
    return raw.replace(b'\r\n', b'\n'), ('CRLF' if crlf else 'LF')


def read_inputs(source: Path, pins):
    inputs = {}
    witnesses = {}
    for name, expected in pins.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe production source name')
        path = source / relative
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or path.is_symlink():
            raise ValueError('production source is not a plain file: ' + name)
        raw = path.read_bytes()
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('production source changed while reading: ' + name)
        canonical, profile = canonical_source(raw)
        if len(canonical) != expected['bytes'] or sha(canonical) != expected['sha256']:
            raise ValueError('production source mismatch: ' + name)
        inputs[name] = canonical
        witnesses[name] = {'rawBytes': len(raw), 'rawSHA256': sha(raw), 'newlineProfile': profile, 'canonicalLFBytes': len(canonical), 'canonicalLFSHA256': sha(canonical)}
    return inputs, witnesses
