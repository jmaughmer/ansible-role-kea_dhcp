#!/usr/bin/python3
"""Just enough of phpIPAM's REST API for kea-phpipam-sync.

Serves /api/<app>/subnets/<id>/ and /api/<app>/subnets/<id>/addresses/ from
/etc/mock-phpipam.json ({"subnets": {"7": [{"ip": ..., "mac": ..., "hostname": ...}]}}),
read on every request so tests can change it between runs. Like phpIPAM, an
empty subnet answers 404 "No addresses found".
"""
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

DATA = "/etc/mock-phpipam.json"
TOKEN = "mock-token"


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - http.server's naming
        if self.headers.get("token") != TOKEN:
            return self.reply(401, {"code": 401, "success": False, "message": "Invalid token"})
        with open(DATA, encoding="utf-8") as fh:
            subnets = json.load(fh)["subnets"]
        match = re.fullmatch(r"/api/[^/]+/subnets/(\d+)/(addresses/)?", self.path)
        if not match or match.group(1) not in subnets:
            return self.reply(404, {"code": 404, "success": False, "message": "Not found"})
        if not match.group(2):
            return self.reply(200, {"code": 200, "success": True, "data": {"id": match.group(1)}})
        addresses = subnets[match.group(1)]
        if not addresses:
            return self.reply(404, {"code": 404, "success": False, "message": "No addresses found"})
        return self.reply(200, {"code": 200, "success": True, "data": addresses})

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
