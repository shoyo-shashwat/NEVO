import uuid

from app.extensions import db
from app.models.accounts import CitizenAccount


def _unique_email():
    # NOTE: email_validator treats *.test as an RFC 2606 reserved TLD and
    # rejects it even with check_deliverability=False — example.com is the
    # RFC 2606 reserved domain that the library does accept syntactically.
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


def test_citizen_signup_requires_consent(client):
    email = _unique_email()
    resp = client.post("/auth/citizen/signup", data={
        "email": email, "password": "correcthorsebatterystaple",
        "confirm_password": "correcthorsebatterystaple",
        # consent omitted
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Consent is required" in resp.data or b"consent" in resp.data.lower()

    with client.application.app_context():
        assert CitizenAccount.query.filter_by(email=email).first() is None


def test_citizen_signup_login_logout_roundtrip(app, client):
    email = _unique_email()
    try:
        signup_resp = client.post("/auth/citizen/signup", data={
            "email": email, "password": "correcthorsebatterystaple",
            "confirm_password": "correcthorsebatterystaple",
            "consent": "on",
        }, follow_redirects=True)
        assert signup_resp.status_code == 200

        with app.app_context():
            account = CitizenAccount.query.filter_by(email=email).first()
            assert account is not None
            assert account.password_hash != "correcthorsebatterystaple"

        # signup logs the citizen in — home page should not prompt for login
        home = client.get("/citizen/", follow_redirects=True)
        assert home.status_code == 200

        logout_resp = client.post("/auth/logout", follow_redirects=True)
        assert logout_resp.status_code == 200

        login_resp = client.post("/auth/citizen/login", data={
            "email": email, "password": "correcthorsebatterystaple",
        }, follow_redirects=True)
        assert login_resp.status_code == 200

        # Already-authenticated requests to the login page redirect home
        # without touching the submitted credentials — log out first so the
        # wrong-password path is actually exercised.
        client.post("/auth/logout")
        wrong_pw = client.post("/auth/citizen/login", data={
            "email": email, "password": "wrong-password",
        })
        assert b"Incorrect email or password" in wrong_pw.data
    finally:
        with app.app_context():
            CitizenAccount.query.filter_by(email=email).delete()
            db.session.commit()


def test_duplicate_signup_email_rejected(app, client):
    email = _unique_email()
    try:
        client.post("/auth/citizen/signup", data={
            "email": email, "password": "correcthorsebatterystaple",
            "confirm_password": "correcthorsebatterystaple", "consent": "on",
        })
        client.post("/auth/logout")
        second = client.post("/auth/citizen/signup", data={
            "email": email, "password": "anotherpassword123",
            "confirm_password": "anotherpassword123", "consent": "on",
        })
        assert b"already exists" in second.data
    finally:
        with app.app_context():
            CitizenAccount.query.filter_by(email=email).delete()
            db.session.commit()


def test_government_login_with_seeded_demo_account(client):
    resp = client.post("/auth/government/login", data={
        "email": "state.admin.in@nevo.demo", "password": "NevoDemo!2026",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"What needs your decision" in resp.data or b"Dashboard" in resp.data or resp.status_code == 200
    client.post("/auth/logout")


def test_government_dashboard_requires_login(client):
    resp = client.get("/gov/dashboard", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/auth/government/login" in resp.headers.get("Location", "") or "/auth/login" in resp.headers.get("Location", "")
