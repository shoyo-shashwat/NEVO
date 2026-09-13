def test_public_pages_render(client):
    resp = client.get("/", follow_redirects=True)
    assert resp.status_code == 200

    for path in ["/citizen/", "/citizen/report", "/citizen/community",
                 "/citizen/map", "/citizen/map/data", "/citizen/track"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"


def test_my_timeline_redirects_when_not_logged_in(client):
    resp = client.get("/citizen/timeline", follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_authenticated_citizen_can_view_timeline_and_profile(client):
    login = client.post("/auth/citizen/login", data={
        "email": "citizen.in@nevo.demo", "password": "NevoDemo!2026",
    })
    assert login.status_code in (302, 303)

    timeline = client.get("/citizen/timeline")
    assert timeline.status_code == 200

    profile = client.get("/auth/citizen/profile")
    assert profile.status_code == 200

    client.post("/auth/logout")
