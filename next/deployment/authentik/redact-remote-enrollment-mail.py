"""Remove verification links from Authentik's Remote enrollment audit events."""

import hashlib
import sys
from pathlib import Path


EXPECTED_SHA256 = "3f1eaaf46c6fbdc4fcd2ace1edf054f5c50577554d8face64a706b3443fd19e3"
ORIGINAL = """    Event.new(
        EventAction.EMAIL_SENT,
        message=f"Email to {', '.join(message_object.to)} sent",
        subject=message_object.subject,
        body=get_email_body(message_object),
        from_email=message_object.from_email,
        to_email=message_object.to,
    ).save()
"""
REPLACEMENT = """    audit_details = {}
    if stage.name != "mixel-remote-enrollment-verify":
        audit_details["body"] = get_email_body(message_object)
    Event.new(
        EventAction.EMAIL_SENT,
        message=f"Email to {', '.join(message_object.to)} sent",
        subject=message_object.subject,
        from_email=message_object.from_email,
        to_email=message_object.to,
        **audit_details,
    ).save()
"""


def main():
    source, destination = map(Path, sys.argv[1:])
    original = source.read_bytes()
    if hashlib.sha256(original).hexdigest() != EXPECTED_SHA256:
        raise SystemExit("Unexpected Authentik mail task. Review the pinned source before patching.")
    if original.count(ORIGINAL.encode()) != 1:
        raise SystemExit("Expected one Authentik email audit event.")
    patched = original.replace(ORIGINAL.encode(), REPLACEMENT.encode())
    compile(patched, str(destination), "exec")
    with destination.open("xb") as output:
        output.write(patched)


if __name__ == "__main__":
    main()
