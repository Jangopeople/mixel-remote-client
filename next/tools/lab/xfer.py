#!/usr/bin/env python3
"""Lab file server: serves ./xfer to the lab VMs (GET) and takes binaries uploaded by them (PUT).

    python3 xfer.py            # binds LAB_BIND (default 10.124.1.97) port LAB_PORT (default 8771)

Only plain file names directly inside ./xfer are served; no directories, no paths. The lab network is not
reachable from outside. QA session files (single use, throwaway QA logins) are served from here for a few minutes
per run: stop the server when the run is over. No secrets are kept in this repository: ./xfer is git-ignored.
"""
import http.server, os, re, socketserver, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xfer")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        name = path.split("?", 1)[0].lstrip("/")
        return os.path.join(ROOT, name) if NAME.match(name) else os.path.join(ROOT, "_none_")

    def list_directory(self, path):
        self.send_error(404)

    def do_PUT(self):
        name = self.path.split("?", 1)[0].lstrip("/")
        if not NAME.match(name):
            return self.send_error(400)
        length = int(self.headers.get("Content-Length", "0"))
        tmp = os.path.join(ROOT, "." + name + ".part")
        with open(tmp, "wb") as out:
            while length > 0:
                chunk = self.rfile.read(min(length, 1 << 20))
                if not chunk:
                    break
                out.write(chunk)
                length -= len(chunk)
        os.replace(tmp, os.path.join(ROOT, name))
        self.send_response(201)
        self.end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("xfer %s\n" % (fmt % args))


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    os.makedirs(ROOT, exist_ok=True)
    bind = os.environ.get("LAB_BIND", "10.124.1.97")
    port = int(os.environ.get("LAB_PORT", "8771"))
    print(f"serving {ROOT} on {bind}:{port}", flush=True)
    Server((bind, port), Handler).serve_forever()
