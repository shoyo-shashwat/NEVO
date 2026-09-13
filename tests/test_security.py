from app.auth.security import (
    hash_password, verify_password, generate_complaint_id,
    generate_reset_token, hash_reset_token,
)


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password(h, "correct horse battery staple")
    assert not verify_password(h, "wrong password")


def test_password_hash_is_not_plaintext():
    h = hash_password("supersecret")
    assert "supersecret" not in h


def test_complaint_id_format():
    cid = generate_complaint_id("in")
    assert cid.startswith("NEVO-IN-")
    parts = cid.split("-")
    assert len(parts) == 4
    assert len(parts[3]) == 8


def test_complaint_ids_are_unique():
    ids = {generate_complaint_id("IN") for _ in range(200)}
    assert len(ids) == 200


def test_reset_token_hash_matches():
    raw, stored_hash = generate_reset_token()
    assert hash_reset_token(raw) == stored_hash
    assert hash_reset_token("a-different-token") != stored_hash
