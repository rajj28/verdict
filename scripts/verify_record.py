"""Verify a downloaded VERDICT judge record using published public keys.

Requires the approved `cryptography` package (or any Ed25519 verifier).
Usage: python scripts/verify_record.py record.json verdict-keys.json
"""
import argparse
import base64
import json
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify(record_document, keys_document):
    record = record_document.get("record")
    signature = record_document.get("signature")
    if not isinstance(record, dict) or not isinstance(signature, str):
        return False, "Malformed signed record."
    if any(row.get("record_id") == record.get("record_id") for row in keys_document.get("revocations", [])):
        return False, "Record has been revoked."
    key = next((row for row in keys_document.get("keys", []) if row.get("kid") == record.get("kid")), None)
    if key is None:
        return False, "No published key matches record kid."
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    try:
        Ed25519PublicKey.from_public_bytes(decode(key["public_key"])).verify(decode(signature), canonical)
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False, "Signature is invalid."
    return True, "Signature is valid."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", help="JSON downloaded from /records/{record_id}?format=json")
    parser.add_argument("keys", help="JSON downloaded from /.well-known/verdict-keys.json")
    args = parser.parse_args()
    try:
        with open(args.record, encoding="utf-8") as stream:
            record = json.load(stream)
        with open(args.keys, encoding="utf-8") as stream:
            keys = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    valid, reason = verify(record, keys)
    print(reason)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
