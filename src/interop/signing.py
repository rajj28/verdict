"""Canonical Ed25519 signing for judge participation records."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from django.conf import settings


def canonical_bytes(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _key_dir() -> Path:
    return Path(settings.DATA_DIR) / "keys"


def _paths() -> list[Path]:
    return sorted(_key_dir().glob("*.private.pem"))


def _generate_key(directory: Path) -> tuple[str, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    public_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    kid = hashlib.sha256(public_bytes).hexdigest()[:16]
    private_path = directory / f"{kid}.private.pem"
    public_path = directory / f"{kid}.public.pem"
    private_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    private_path.chmod(0o600)
    public_path.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ))
    (directory / "current").write_text(kid, encoding="ascii")
    return kid, key


def ensure_current_key() -> tuple[str, Ed25519PrivateKey]:
    """Create a persistent instance key once; keep older keys for verification."""
    directory = _key_dir()
    directory.mkdir(parents=True, exist_ok=True)
    private_paths = _paths()
    if private_paths:
        try:
            current_kid = (directory / "current").read_text(encoding="ascii").strip()
        except OSError:
            current_kid = ""
        path = next((item for item in private_paths if item.name == f"{current_kid}.private.pem"), None)
        if path is None:
            path = max(private_paths, key=lambda item: item.stat().st_mtime_ns)
            (directory / "current").write_text(path.name.removesuffix(".private.pem"), encoding="ascii")
        raw = path.read_bytes()
        key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError(f"Unexpected signing key type in {path}")
        return path.name.removesuffix(".private.pem"), key

    return _generate_key(directory)


def rotate_key() -> str:
    """Install a new signing key without removing old public keys."""
    directory = _key_dir()
    directory.mkdir(parents=True, exist_ok=True)
    kid, _ = _generate_key(directory)
    return kid


def public_key_document() -> dict:
    keys = []
    for private_path in _paths():
        kid = private_path.name.removesuffix(".private.pem")
        public_path = _key_dir() / f"{kid}.public.pem"
        public = serialization.load_pem_public_key(public_path.read_bytes())
        if not isinstance(public, Ed25519PublicKey):
            raise RuntimeError(f"Unexpected public signing key type in {public_path}")
        raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        created = datetime.fromtimestamp(private_path.stat().st_mtime, timezone.utc).isoformat()
        keys.append({"kid": kid, "public_key": _b64(raw), "created_at": created})

    from interop.models import JudgeParticipationRecord

    revoked = list(
        JudgeParticipationRecord.objects.filter(revoked_at__isnull=False)
        .order_by("record_id")
        .values("record_id", "revoked_at")
    )
    return {
        "algorithm": "Ed25519",
        "keys": keys,
        "revocations": [
            {"record_id": row["record_id"], "revoked_at": row["revoked_at"].isoformat()}
            for row in revoked
        ],
    }


def sign_record(record: dict) -> tuple[str, str]:
    kid, private = ensure_current_key()
    record["kid"] = kid
    return kid, _b64(private.sign(canonical_bytes(record)))


def verify_record_signature(record: dict, signature: str, public_key: str) -> bool:
    key = Ed25519PublicKey.from_public_bytes(_unb64(public_key))
    try:
        key.verify(_unb64(signature), canonical_bytes(record))
    except InvalidSignature:
        return False
    return True


def record_document(record) -> dict:
    return {"record": record.record, "signature": record.signature}
