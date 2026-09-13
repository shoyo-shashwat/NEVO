# auth/forms.py
# Flask-WTF forms — CSRF protection comes free from FlaskForm (app-wide
# CSRFProtect() is also enabled in app/__init__.py as a second layer for any
# non-WTForms POST endpoint).

from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, BooleanField, SelectField, HiddenField,
)
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional


class CitizenSignupForm(FlaskForm):
    full_name = StringField("Full name", validators=[Optional(), Length(max=200)])
    email = StringField("Email", validators=[DataRequired(), Email(check_deliverability=False), Length(max=320)])
    phone = StringField("Phone (optional)", validators=[Optional(), Length(max=20)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    consent = BooleanField(
        "I consent to NEVO storing and processing this report to route it to the "
        "relevant government authority.",
        validators=[DataRequired(message="Consent is required to create an account.")],
    )


class CitizenLoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(check_deliverability=False)])
    password = PasswordField("Password", validators=[DataRequired()])


class GovernmentLoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(check_deliverability=False)])
    password = PasswordField("Password", validators=[DataRequired()])


class PasswordResetRequestForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(check_deliverability=False)])


class PasswordResetConfirmForm(FlaskForm):
    token = HiddenField(validators=[DataRequired()])
    password = PasswordField("New password", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )


class ProvisionGovernmentAccountForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=200)])
    email = StringField("Email", validators=[DataRequired(), Email(check_deliverability=False), Length(max=320)])
    role = SelectField(
        "Role",
        choices=[
            ("national_admin", "National Administrator"),
            ("state_admin", "State Administrator"),
            ("district_officer", "District Officer"),
            ("department_officer", "Department Officer"),
            ("analyst", "Analyst"),
            ("reviewer", "Reviewer"),
        ],
        validators=[DataRequired()],
    )
    region_id = SelectField("Region (state/district)", validators=[Optional()], choices=[])
    department_id = SelectField("Department", validators=[Optional()], choices=[])
