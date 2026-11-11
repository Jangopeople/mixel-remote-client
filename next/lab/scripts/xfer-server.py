# Lab file exchange with the Ubuntu VM: GET serves files, PUT stores them as up-<name>.
import http.server, os, sys
root = sys.argv[1]
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=root, **k)
    def do_PUT(self):
        name = os.path.basename(self.path)
        if not name or name.startswith('.'): self.send_error(400); return
        n = int(self.headers.get('content-length', 0))
        with open(os.path.join(root, 'up-' + name), 'wb') as f:
            while n > 0:
                b = self.rfile.read(min(n, 1 << 20)); f.write(b); n -= len(b)
        self.send_response(201); self.end_headers()
http.server.ThreadingHTTPServer(('10.124.1.97', 8765), H).serve_forever()
