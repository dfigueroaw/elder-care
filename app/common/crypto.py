"""ID generation and secret hashing."""

import base64
import hashlib
import hmac
import secrets
import uuid

# Fixed namespace so a (deviceId, dedupeKey) pair always maps to the same alertId
ALERT_NAMESPACE = uuid.UUID("6f1c2a52-9a43-4f57-8f0e-2b7d0c6a9e31")

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN = 2**14, 8, 1, 32


def new_id():
    return str(uuid.uuid4())


def alert_id_for(device_id, dedupe_key):
    return str(uuid.uuid5(ALERT_NAMESPACE, f"{device_id}:{dedupe_key}"))


def sha256_hex(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_device_secret():
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def hash_secret(secret):
    """scrypt (memory-hard, stdlib) in place of bcrypt/argon2 so no native deps are needed."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_secret(secret, stored):
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        candidate = hashlib.scrypt(
            secret.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(candidate, expected)
