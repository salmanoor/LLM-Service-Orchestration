"""Single-capability HTTP service process for the indoor inspection study."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT))
import poc

CAPABILITIES = {"measure", "segment", "compare", "rule", "provenance"}


def payload_for(scans: list[dict], capability: str) -> dict:
    if capability == "measure":
        data = poc.measurement_service(scans)
    elif capability == "segment":
        data = [{"room": row["room"], "sensor": row["sensor"], "orientation_area_m2": row["orientation_area_m2"], "sha256": row["sha256"]} for row in scans]
    elif capability == "compare":
        data = poc.compare_service(scans)
    elif capability == "rule":
        data = poc.rule_service("door_clear_width", scans)
    else:
        data = [{"room": row["room"], "sensor": row["sensor"], "file": row["file"], "sha256": row["sha256"]} for row in scans]
    return {"service": capability, "version": "1.0", "dataset": "aalto_hall101", "result": data}


def make_handler(name: str, fault: str, scans: list[dict]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "IndoorInspectionService/1.0"

        def log_message(self, *_args):
            return

        def send_json(self, status: int, data: dict):
            body = json.dumps(data, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/health":
                return self.send_json(404, {"error": "not_found"})
            self.send_json(200, {"service": name, "version": "1.0", "status": "ready"})

        def do_POST(self):
            if self.path != f"/v1/{name}":
                return self.send_json(404, {"error": "not_found"})
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or int(length) > 4096:
                return self.send_json(413, {"error": "request_too_large"})
            if self.headers.get_content_type() != "application/json":
                return self.send_json(415, {"error": "json_required"})
            try:
                request = json.loads(self.rfile.read(int(length)))
            except (ValueError, UnicodeDecodeError):
                return self.send_json(400, {"error": "invalid_json"})
            if not isinstance(request, dict) or set(request) != {"dataset"} or request.get("dataset") != "aalto_hall101":
                return self.send_json(400, {"error": "invalid_request"})
            if fault == "unavailable":
                return self.send_json(503, {"error": "service_unavailable"})
            if fault == "slow":
                time.sleep(1.0)
            response = payload_for(scans, name)
            if fault == "malformed":
                body = b"{not-json"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            self.send_json(200, response)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", choices=sorted(CAPABILITIES), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--fault", choices=("none", "unavailable", "malformed", "slow"), default="none")
    args = parser.parse_args()
    # Each independently launched process parses and owns its source-data snapshot.
    scans = poc.scan_service()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.name, args.fault, scans))
    server.serve_forever()


if __name__ == "__main__":
    main()
