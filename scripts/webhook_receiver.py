"""Local signed-webhook demo receiver; uses only the Python standard library."""
import hashlib
import hmac
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(min(length, 1_000_000))
        secret = os.environ.get("WEBHOOK_SECRET", "").encode("utf-8")
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        supplied = self.headers.get("X-Verdict-Signature", "")
        valid = bool(secret) and hmac.compare_digest(expected, supplied)
        print({
            "valid_signature": valid,
            "event": self.headers.get("X-Verdict-Event", ""),
            "delivery": self.headers.get("X-Verdict-Delivery", ""),
            "payload": body.decode("utf-8", "replace"),
        }, flush=True)
        self.send_response(202 if valid else 401)
        self.end_headers()
        self.wfile.write(b"accepted" if valid else b"invalid signature")

    def log_message(self, format, *args):
        print(format % args, flush=True)


def main():
    host = os.environ.get("WEBHOOK_RECEIVER_HOST", "127.0.0.1")
    port = int(os.environ.get("WEBHOOK_RECEIVER_PORT", "8765"))
    if not os.environ.get("WEBHOOK_SECRET"):
        raise SystemExit("Set WEBHOOK_SECRET to the endpoint secret before starting the receiver.")
    print(f"Listening on http://{host}:{port}; configure WEBHOOKS_ALLOW_PRIVATE=1 for this offline demo.")
    HTTPServer((host, port), Receiver).serve_forever()


if __name__ == "__main__":
    main()
