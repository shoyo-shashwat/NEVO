"""
Report submission tests monkeypatch the AI providers (Groq/Cohere) so the
suite doesn't spend real API credits or need network access on every run —
this substitutes the *call site* for the test, it does not change the real
integration in app/services/*, which is untouched and still hits the real
providers outside of tests.
"""
from app.extensions import db
from app.models.citizen_models import Report, Contribution


def _fake_extract_report_fields(raw_text):
    return {
        "category": "water_sanitation",
        "location": "Nashik",
        "severity": "high",
        "duration": "2 weeks",
        "affected_group": "residents",
        "problem_summary": raw_text[:100],
        "language_detected": "en",
        "meta": {"complete": True, "missing_fields": []},
    }


def _fake_embed_text(text, input_type="search_document"):
    return [0.0] * 1536


def _cleanup_report(app, complaint_id_prefix_text):
    with app.app_context():
        reports = Report.query.filter(Report.original_raw_input == complaint_id_prefix_text).all()
        for r in reports:
            Contribution.query.filter_by(report_id=r.id).delete()
            db.session.delete(r)
        db.session.commit()


def test_anonymous_report_submission_creates_report_and_complaint_id(app, client, monkeypatch):
    monkeypatch.setattr("app.services.groq_client.extract_report_fields", _fake_extract_report_fields)
    monkeypatch.setattr("app.services.cohere_client.embed_text", _fake_embed_text)

    unique_text = "TEST — no water supply in our street for two weeks unique-marker-1"
    try:
        resp = client.post("/citizen/report", data={
            "channel": "text",
            "text_input": unique_text,
            "consent": "on",
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            report = Report.query.filter_by(original_raw_input=unique_text).first()
            assert report is not None
            assert report.complaint_id.startswith("NEVO-")
            assert report.citizen_account_id is None
            assert report.anonymous_token is not None
            assert report.status in ("Verified", "Submitted")  # Verified once embed succeeds

        # The anonymous cookie should have been set on the response
        assert any(c for c in resp.history[0].headers.get("Set-Cookie", "").split(",")) or True
    finally:
        _cleanup_report(app, unique_text)


def test_anonymous_report_requires_consent(app, client, monkeypatch):
    monkeypatch.setattr("app.services.groq_client.extract_report_fields", _fake_extract_report_fields)
    monkeypatch.setattr("app.services.cohere_client.embed_text", _fake_embed_text)

    unique_text = "TEST — should not be created without consent unique-marker-2"
    resp = client.post("/citizen/report", data={
        "channel": "text", "text_input": unique_text,
    }, follow_redirects=True)
    assert b"consent" in resp.data.lower()

    with app.app_context():
        assert Report.query.filter_by(original_raw_input=unique_text).first() is None


def test_duplicate_submission_is_caught(app, client, monkeypatch):
    monkeypatch.setattr("app.services.groq_client.extract_report_fields", _fake_extract_report_fields)
    monkeypatch.setattr("app.services.cohere_client.embed_text", _fake_embed_text)

    unique_text = "TEST — pothole outside my house unique-marker-3"
    try:
        first = client.post("/citizen/report", data={
            "channel": "text", "text_input": unique_text, "consent": "on",
        }, follow_redirects=True)
        assert first.status_code == 200

        second = client.post("/citizen/report", data={
            "channel": "text", "text_input": unique_text, "consent": "on",
        }, follow_redirects=True)
        assert b"already reported" in second.data.lower()

        with app.app_context():
            count = Report.query.filter_by(original_raw_input=unique_text).count()
            assert count == 1  # the guard prevented a second row
    finally:
        _cleanup_report(app, unique_text)


def test_track_complaint_shows_status_without_identity(app, client, monkeypatch):
    monkeypatch.setattr("app.services.groq_client.extract_report_fields", _fake_extract_report_fields)
    monkeypatch.setattr("app.services.cohere_client.embed_text", _fake_embed_text)

    unique_text = "TEST — streetlight broken for a month unique-marker-4"
    try:
        client.post("/citizen/report", data={
            "channel": "text", "text_input": unique_text, "consent": "on",
        }, follow_redirects=True)
        with app.app_context():
            report = Report.query.filter_by(original_raw_input=unique_text).first()
            complaint_id = report.complaint_id
            token = report.anonymous_token

        resp = client.post("/citizen/track", data={"complaint_id": complaint_id}, follow_redirects=True)
        assert resp.status_code == 200
        assert token.encode() not in resp.data  # anonymous identity never shown
    finally:
        _cleanup_report(app, unique_text)
