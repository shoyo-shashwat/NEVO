# app/cli.py
#
# Flask CLI commands. The only one right now is the one-time bootstrap for
# the very first national_admin — every government account after that is
# created through the authenticated provisioning UI
# (auth.provision_government_account), not the CLI (Phase 0 design §3).

import click


def register_cli(app):
    @app.cli.command("create-national-admin")
    @click.option("--email", required=True)
    @click.option("--password", required=True)
    @click.option("--full-name", required=True)
    @click.option("--country-code", default="IN")
    def create_national_admin(email, password, full_name, country_code):
        """One-time bootstrap: flask create-national-admin --email ... --password ... --full-name ..."""
        from app.extensions import db
        from app.models.accounts import GovernmentAccount
        from app.models.shared import Country
        from app.auth.security import hash_password

        existing = GovernmentAccount.query.filter_by(email=email.lower().strip()).first()
        if existing:
            click.echo(f"An account already exists for {email}.")
            return

        country = Country.query.filter_by(code=country_code.upper()).first()
        if not country:
            click.echo(f"No country with code {country_code} found — run the seed script first.")
            return

        account = GovernmentAccount(
            email=email.lower().strip(),
            password_hash=hash_password(password),
            full_name=full_name,
            role="national_admin",
            country_id=country.id,
            region_id=None,
            provisioned_by=None,
        )
        db.session.add(account)
        db.session.commit()
        click.echo(f"national_admin created: {account.email} (id={account.id})")
