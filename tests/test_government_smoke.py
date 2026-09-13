"""
Smoke tests: log in as each seeded government role and hit every government
page, asserting no 500s. Catches template/variable-mismatch regressions
that unit tests on isolated functions wouldn't.
"""
import pytest

from app.models.demand_cluster import DemandCluster


DEMO_ACCOUNTS = [
    "national.admin.in@nevo.demo",
    "state.admin.in@nevo.demo",
    "district.officer.in@nevo.demo",
    "health.officer.in@nevo.demo",
    "analyst.in@nevo.demo",
    "reviewer.in@nevo.demo",
]


@pytest.mark.parametrize("email", DEMO_ACCOUNTS)
def test_dashboard_and_projects_render_for_every_role(app, client, email):
    login = client.post("/auth/government/login", data={"email": email, "password": "NevoDemo!2026"})
    assert login.status_code in (302, 303)

    dashboard = client.get("/gov/dashboard")
    assert dashboard.status_code == 200, f"{email} dashboard failed"

    projects = client.get("/gov/projects")
    assert projects.status_code == 200, f"{email} projects failed"

    map_page = client.get("/gov/map")
    assert map_page.status_code == 200, f"{email} map failed"

    client.post("/auth/logout")


def test_evidence_detail_and_decision_workspace_render(app, client):
    with app.app_context():
        cluster = DemandCluster.query.first()
        cluster_id = cluster.id

    client.post("/auth/government/login", data={"email": "state.admin.in@nevo.demo", "password": "NevoDemo!2026"})

    evidence = client.get(f"/gov/demand/{cluster_id}")
    assert evidence.status_code == 200

    decide = client.get(f"/gov/demand/{cluster_id}/decide")
    assert decide.status_code == 200

    client.post("/auth/logout")


def test_analyst_cannot_post_decision(app, client):
    with app.app_context():
        cluster = DemandCluster.query.first()
        cluster_id = cluster.id

    client.post("/auth/government/login", data={"email": "analyst.in@nevo.demo", "password": "NevoDemo!2026"})
    resp = client.post(f"/gov/demand/{cluster_id}/decide", data={
        "decision_type": "Prioritize", "reason": "test",
    })
    assert resp.status_code == 403
    client.post("/auth/logout")


def test_admin_page_requires_national_admin(app, client):
    client.post("/auth/government/login", data={"email": "state.admin.in@nevo.demo", "password": "NevoDemo!2026"})
    resp = client.get("/gov/admin")
    assert resp.status_code == 403
    client.post("/auth/logout")

    client.post("/auth/government/login", data={"email": "national.admin.in@nevo.demo", "password": "NevoDemo!2026"})
    resp = client.get("/gov/admin")
    assert resp.status_code == 200
    client.post("/auth/logout")
