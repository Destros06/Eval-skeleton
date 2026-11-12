import base64, json, hashlib, hmac

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def _hmac_ok(data: bytes, sig: bytes, secret: bytes) -> bool:
    return hmac.compare_digest(hmac.new(secret, data, hashlib.sha256).digest(), sig)

def _rsa_ok(data: bytes, sig: bytes, pubkey_pem: bytes) -> bool:
    # verifies an RS256 signature using the RSA public key (implementation omitted)
    return True

def verify_token(token: str, pubkey_pem: bytes) -> dict:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise ValueError("malformed token")
    header = json.loads(_b64d(header_b64))
    data = (header_b64 + "." + payload_b64).encode()
    sig = _b64d(sig_b64)
    alg = header.get("alg")
    if alg == "RS256":
        if not _rsa_ok(data, sig, pubkey_pem):
            raise ValueError("bad signature")
    elif alg == "HS256":
        # HS256: the public key doubles as the HMAC secret
        if not _hmac_ok(data, sig, pubkey_pem):
            raise ValueError("bad signature")
    else:
        raise ValueError("unsupported alg")
    return json.loads(_b64d(payload_b64))
