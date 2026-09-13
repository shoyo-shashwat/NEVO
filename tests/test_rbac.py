from app.auth.rbac import scoped_region_ids, scoped_department_ids
from app.models.accounts import GovernmentAccount
from app.models.shared import AdministrativeRegion, Country


def test_national_admin_is_unscoped(app, db_session):
    with app.app_context():
        account = GovernmentAccount.query.filter_by(role="national_admin").first()
        assert account is not None
        assert scoped_region_ids(account) is None


def test_district_officer_scope_includes_own_region_and_descendants(app, db_session):
    with app.app_context():
        account = GovernmentAccount.query.filter_by(role="state_admin", email="state.admin.in@nevo.demo").first()
        assert account is not None
        ids = scoped_region_ids(account)
        assert ids is not None
        assert account.region_id in ids
        # Maharashtra (state) should include its seeded districts
        nashik = AdministrativeRegion.query.filter_by(name="Nashik").first()
        assert nashik is not None
        assert nashik.id in ids


def test_department_officer_is_department_scoped(app, db_session):
    with app.app_context():
        account = GovernmentAccount.query.filter_by(role="department_officer").first()
        assert account is not None
        dept_ids = scoped_department_ids(account)
        assert dept_ids == [account.department_id]


def test_analyst_is_not_department_scoped(app, db_session):
    with app.app_context():
        account = GovernmentAccount.query.filter_by(role="analyst").first()
        assert account is not None
        assert scoped_department_ids(account) is None


def test_no_account_means_no_access():
    assert scoped_region_ids(None) == []
    assert scoped_department_ids(None) == []
