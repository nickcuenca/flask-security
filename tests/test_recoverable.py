"""
test_recoverable
~~~~~~~~~~~~~~~~

Recoverable functionality tests

:copyright: (c) 2019-2025 by J. Christopher Wagner (jwag).
:license: MIT, see LICENSE for more details.
"""

from datetime import date, timedelta
import re
from urllib.parse import parse_qsl, urlsplit

import pytest
from flask import Flask
from freezegun import freeze_time
from wtforms.fields import StringField
from wtforms.validators import Length
from tests.test_utils import (
    authenticate,
    capture_flashes,
    capture_reset_password_requests,
    check_location,
    get_form_input_value,
    logout,
    populate_data,
)

from flask_security.core import Security, UserMixin
from flask_security.forms import ForgotPasswordForm, LoginForm
from flask_security.signals import (
    password_reset,
    reset_password_instructions_sent,
    username_recovery_email_sent,
)

pytestmark = pytest.mark.recoverable()


def test_recoverable_flag(app, clients, get_message):
    recorded_resets = []
    recorded_instructions_sent = []

    @password_reset.connect_via(app)
    def on_password_reset(app, user):
        recorded_resets.append(user)

    @reset_password_instructions_sent.connect_via(app)
    def on_instructions_sent(app, **kwargs):
        assert isinstance(app, Flask)
        assert isinstance(kwargs["user"], UserMixin)
        assert isinstance(kwargs["token"], str)
        recorded_instructions_sent.append(kwargs["user"])

    # Test the reset view
    response = clients.get("/reset")
    assert b"<h1>Send password reset instructions</h1>" in response.data
    assert re.search(b'<input[^>]*type="email"[^>]*>', response.data)

    # Test submitting email to reset password creates a token and sends email
    with capture_reset_password_requests() as requests:
        response = clients.post(
            "/reset", data=dict(email="joe@lp.com"), follow_redirects=True
        )

    assert len(recorded_instructions_sent) == 1
    assert len(app.mail.outbox) == 1
    assert response.status_code == 200
    assert get_message("PASSWORD_RESET_REQUEST", email="joe@lp.com") in response.data
    token = requests[0]["token"]

    # Test view for reset token
    response = clients.get("/reset/" + token)
    assert b"<h1>Reset password</h1>" in response.data

    # Test submitting a new password but leave out confirm
    response = clients.post(
        "/reset/" + token, data={"password": "newpassword"}, follow_redirects=True
    )
    assert get_message("PASSWORD_NOT_PROVIDED") in response.data
    assert len(recorded_resets) == 0

    # Test submitting a new password
    response = clients.post(
        "/reset/" + token,
        data={"password": "awesome sunset", "password_confirm": "awesome sunset"},
        follow_redirects=True,
    )

    assert get_message("PASSWORD_RESET_NO_LOGIN") in response.data
    assert len(recorded_resets) == 1

    logout(clients)

    # Test logging in with the new password
    response = authenticate(
        clients, "joe@lp.com", "awesome sunset", follow_redirects=True
    )
    assert b"Welcome joe@lp.com" in response.data

    logout(clients)

    # Test invalid email
    response = clients.post(
        "/reset", data=dict(email="bogus@lp.com"), follow_redirects=True
    )
    assert get_message("USER_DOES_NOT_EXIST") in response.data

    logout(clients)

    # Test invalid token
    response = clients.post(
        "/reset/bogus",
        data={"password": "awesome sunset", "password_confirm": "awesome sunset"},
        follow_redirects=True,
    )
    assert get_message("INVALID_RESET_PASSWORD_TOKEN") in response.data

    # Test mangled token
    token = (
        "WyIxNjQ2MzYiLCIxMzQ1YzBlZmVhM2VhZjYwODgwMDhhZGU2YzU0MzZjMiJd."
        "BZEw_Q.lQyo3npdPZtcJ_sNHVHP103syjM"
        "&url_id=fbb89a8328e58c181ea7d064c2987874bc54a23d"
    )
    response = clients.post(
        "/reset/" + token,
        data={"password": "newpassword", "password_confirm": "newpassword"},
        follow_redirects=True,
    )
    assert get_message("INVALID_RESET_PASSWORD_TOKEN") in response.data


@pytest.mark.confirmable()
@pytest.mark.registerable()
@pytest.mark.settings(requires_confirmation_error_view="/confirm")
def test_requires_confirmation_error_redirect(app, clients):
    data = dict(email="jyl@lp.com", password="awesome sunset")
    clients.post("/register", data=data)

    response = clients.post(
        "/reset", data=dict(email="jyl@lp.com"), follow_redirects=True
    )
    assert b"send_confirmation_form" in response.data
    assert b"jyl@lp.com" in response.data


@pytest.mark.settings()
def test_recoverable_json(app, client, get_message):
    recorded_resets = []
    recorded_instructions_sent = []

    @password_reset.connect_via(app)
    def on_password_reset(app, user):
        recorded_resets.append(user)

    @reset_password_instructions_sent.connect_via(app)
    def on_instructions_sent(app, **kwargs):
        recorded_instructions_sent.append(kwargs["user"])

    with capture_flashes() as flashes:
        # Test reset password creates a token and sends email
        with capture_reset_password_requests() as requests:
            response = client.post(
                "/reset",
                json=dict(email="joe@lp.com"),
                headers={"Content-Type": "application/json"},
            )
            assert response.headers["Content-Type"] == "application/json"

        assert len(recorded_instructions_sent) == 1
        assert len(app.mail.outbox) == 1
        assert response.status_code == 200
        token = requests[0]["token"]

        # Test invalid email
        response = client.post(
            "/reset",
            json=dict(email="whoknows@lp.com"),
        )
        assert response.status_code == 400
        assert response.json["response"]["errors"][0].encode("utf-8") == get_message(
            "USER_DOES_NOT_EXIST"
        )

        # Test submitting a new password but leave out 'confirm'
        response = client.post(
            "/reset/" + token,
            json=dict(password="newpassword"),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json["response"]["errors"][0].encode("utf-8") == get_message(
            "PASSWORD_NOT_PROVIDED"
        )

        # Test submitting a new password
        response = client.post(
            "/reset/" + token + "?include_auth_token",
            json=dict(password="awesome sunset", password_confirm="awesome sunset"),
        )
        assert not response.json["response"]
        assert len(recorded_resets) == 1

        # reset automatically logs user in
        logout(client)

        # Test logging in with the new password
        response = client.post(
            "/login?include_auth_token",
            json=dict(email="joe@lp.com", password="awesome sunset"),
            headers={"Content-Type": "application/json"},
        )
        assert all(
            k in response.json["response"]["user"]
            for k in ["email", "authentication_token"]
        )

        logout(client)

        # Use token again - should fail since already have set new password.
        response = client.post(
            "/reset/" + token,
            json=dict(password="newpassword", password_confirm="newpassword"),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert len(recorded_resets) == 1

        # Test invalid token
        response = client.post(
            "/reset/bogus",
            json=dict(password="newpassword", password_confirm="newpassword"),
            headers={"Content-Type": "application/json"},
        )
        assert response.json["response"]["errors"][0].encode("utf-8") == get_message(
            "INVALID_RESET_PASSWORD_TOKEN"
        )
    assert len(flashes) == 0


def test_recoverable_template(app, client, get_message):
    # Check contents of email template - this uses a test template
    # in order to check all context vars since the default template
    # doesn't have all of them.
    with capture_reset_password_requests() as resets:
        response = client.post(
            "/reset", data=dict(email="joe@lp.com"), follow_redirects=True
        )
        outbox = app.mail.outbox
        assert len(outbox) == 1
        matcher = re.findall(r"\w+:.*", outbox[0].body, re.IGNORECASE)
        # should be 4 - link, email, token, config item
        assert matcher[1].split(":")[1] == "joe@lp.com"
        assert matcher[2].split(":")[1] == resets[0]["reset_token"]
        assert matcher[3].split(":")[1] == "True"  # register_blueprint
        assert matcher[4].split(":")[1] == "/reset"  # SECURITY_RESET_URL

        # check link
        link = matcher[0].split(":", 1)[1]
        response = client.get(link, follow_redirects=True)
        assert b"Reset Password" in response.data


def test_recover_invalidates_session(app, client):
    # Make sure that if we reset our password - prior sessions are invalidated.

    other_client = app.test_client()
    authenticate(other_client)
    response = other_client.get("/profile", follow_redirects=True)
    assert b"Profile Page" in response.data

    # use normal client to reset password
    with capture_reset_password_requests() as requests:
        response = client.post(
            "/reset",
            json=dict(email="matt@lp.com"),
        )
        assert response.headers["Content-Type"] == "application/json"

    assert response.status_code == 200
    token = requests[0]["token"]

    # Test submitting a new password
    response = client.post(
        "/reset/" + token + "?include_auth_token",
        json=dict(password="awesome sunset", password_confirm="awesome sunset"),
    )
    assert response.status_code == 200

    # try to access protected endpoint with old session - shouldn't work
    response = other_client.get("/profile")
    assert response.status_code == 302
    assert response.location == "/login?next=/profile"


def test_login_form_description(app, sqlalchemy_datastore):
    app.security = Security(app, datastore=sqlalchemy_datastore)
    with app.test_request_context("/login"):
        login_form = LoginForm()
        expected = '<a href="/reset">Forgot password?</a>'
        assert login_form.password.description == expected


@pytest.mark.settings(reset_password_within="1 milliseconds")
def test_expired_reset_token(client, get_message):
    # Note that we need relatively new-ish date since session cookies also expire.
    with freeze_time(date.today() + timedelta(days=-1)):
        with capture_reset_password_requests() as requests:
            client.post("/reset", data=dict(email="joe@lp.com"), follow_redirects=True)

    user = requests[0]["user"]
    token = requests[0]["token"]

    with capture_flashes() as flashes:
        msg = get_message(
            "PASSWORD_RESET_EXPIRED", within="1 milliseconds", email=user.email
        )

        # Test getting reset form with expired token
        response = client.get("/reset/" + token, follow_redirects=True)
        assert msg in response.data

        # Test trying to reset password with expired token
        response = client.post(
            "/reset/" + token,
            data={"password": "newpassword", "password_confirm": "newpassword"},
            follow_redirects=True,
        )

        assert msg in response.data
    assert len(flashes) == 2


def test_bad_reset_token(client, get_message):
    # Test invalid token - get form
    response = client.get("/reset/bogus", follow_redirects=True)
    assert get_message("INVALID_RESET_PASSWORD_TOKEN") in response.data

    # Test invalid token - reset password
    response = client.post(
        "/reset/bogus",
        data={"password": "newpassword", "password_confirm": "newpassword"},
        follow_redirects=True,
    )
    assert get_message("INVALID_RESET_PASSWORD_TOKEN") in response.data

    # Test mangled token
    token = (
        "WyIxNjQ2MzYiLCIxMzQ1YzBlZmVhM2VhZjYwODgwMDhhZGU2YzU0MzZjMiJd."
        "BZEw_Q.lQyo3npdPZtcJ_sNHVHP103syjM"
        "&url_id=fbb89a8328e58c181ea7d064c2987874bc54a23d"
    )
    response = client.post(
        "/reset/" + token,
        data={"password": "newpassword", "password_confirm": "newpassword"},
        follow_redirects=True,
    )
    assert get_message("INVALID_RESET_PASSWORD_TOKEN") in response.data


def test_reset_token_deleted_user(app, client, get_message):
    with capture_reset_password_requests() as requests:
        client.post("/reset", data=dict(email="gene@lp.com"), follow_redirects=True)

    token = requests[0]["token"]

    # Delete user
    with app.app_context():
        # load user (and role) to get into session so cascade delete works.
        user = app.security.datastore.find_user(email="gene@lp.com")
        app.security.datastore.delete(user)
        app.security.datastore.commit()

    response = client.post(
        "/reset/" + token,
        data={"password": "newpassword", "password_confirm": "newpassword"},
        follow_redirects=True,
    )

    msg = get_message("INVALID_RESET_PASSWORD_TOKEN")
    assert msg in response.data


def test_used_reset_token(client, get_message):
    with capture_reset_password_requests() as requests:
        client.post("/reset", data=dict(email="joe@lp.com"), follow_redirects=True)

    token = requests[0]["token"]

    # use the token
    response = client.post(
        "/reset/" + token,
        data={"password": "awesome sunset", "password_confirm": "awesome sunset"},
        follow_redirects=True,
    )

    assert get_message("PASSWORD_RESET_NO_LOGIN") in response.data

    logout(client)

    # attempt to use it a second time
    response2 = client.post(
        "/reset/" + token,
        data={"password": "otherpassword", "password_confirm": "otherpassword"},
        follow_redirects=True,
    )

    msg = get_message("INVALID_RESET_PASSWORD_TOKEN")
    assert msg in response2.data


def test_reset_passwordless_user(client, get_message):
    with capture_reset_password_requests() as requests:
        client.post("/reset", data=dict(email="jess@lp.com"), follow_redirects=True)

    token = requests[0]["token"]

    # use the token
    response = client.post(
        "/reset/" + token,
        data={"password": "awesome sunset", "password_confirm": "awesome sunset"},
        follow_redirects=True,
    )

    assert get_message("PASSWORD_RESET_NO_LOGIN") in response.data


@pytest.mark.settings(reset_url="/custom_reset")
def test_custom_reset_url(client):
    response = client.get("/custom_reset")
    assert response.status_code == 200


@pytest.mark.settings(
    reset_password_template="custom_security/reset_password.html",
    forgot_password_template="custom_security/forgot_password.html",
)
def test_custom_reset_templates(client):
    response = client.get("/reset")
    assert b"CUSTOM FORGOT PASSWORD" in response.data

    with capture_reset_password_requests() as requests:
        client.post("/reset", data=dict(email="joe@lp.com"), follow_redirects=True)
        token = requests[0]["token"]

    response = client.get("/reset/" + token)
    assert b"CUSTOM RESET PASSWORD" in response.data


@pytest.mark.settings(
    redirect_host="myui.com:8090",
    redirect_behavior="spa",
    reset_view="/reset-redirect",
)
def test_spa_get(app, client):
    """
    Test 'single-page-application' style redirects
    This uses json only.
    """
    with capture_reset_password_requests() as requests:
        response = client.post(
            "/reset",
            json=dict(email="joe@lp.com"),
        )
        assert response.headers["Content-Type"] == "application/json"
        assert "user" not in response.json["response"]
    token = requests[0]["token"]

    response = client.get("/reset/" + token)
    assert response.status_code == 302
    split = urlsplit(response.headers["Location"])
    assert "myui.com:8090" == split.netloc
    assert "/reset-redirect" == split.path
    qparams = dict(parse_qsl(split.query))
    # we shouldn't be showing PII
    assert "email" not in qparams
    assert qparams["token"] == token


@pytest.mark.settings(
    reset_password_within="1 milliseconds",
    redirect_host="localhost:8081",
    redirect_behavior="spa",
    reset_error_view="/reset-error",
)
def test_spa_get_bad_token(app, client, get_message):
    """Test expired and invalid token"""
    with capture_flashes() as flashes:
        # Note that we need relatively new-ish date since session cookies also expire.
        with freeze_time(date.today() + timedelta(days=-1)):
            with capture_reset_password_requests() as requests:
                response = client.post(
                    "/reset",
                    json=dict(email="joe@lp.com"),
                    headers={"Content-Type": "application/json"},
                )
                assert response.headers["Content-Type"] == "application/json"
                assert "user" not in response.json["response"]
            token = requests[0]["token"]

        response = client.get("/reset/" + token)
        assert response.status_code == 302
        split = urlsplit(response.headers["Location"])
        assert "localhost:8081" == split.netloc
        assert "/reset-error" == split.path
        qparams = dict(parse_qsl(split.query))
        # on error - no PII should be returned.
        assert "error" in qparams
        assert "identity" not in qparams
        assert "email" not in qparams

        msg = get_message(
            "PASSWORD_RESET_EXPIRED", within="1 milliseconds", email="joe@lp.com"
        )
        assert msg == qparams["error"].encode("utf-8")

        # Test mangled token
        token = (
            "WyIxNjQ2MzYiLCIxMzQ1YzBlZmVhM2VhZjYwODgwMDhhZGU2YzU0MzZjMiJd."
            "BZEw_Q.lQyo3npdPZtcJ_sNHVHP103syjM"
            "&url_id=fbb89a8328e58c181ea7d064c2987874bc54a23d"
        )
        response = client.get("/reset/" + token)
        assert response.status_code == 302
        split = urlsplit(response.headers["Location"])
        assert "localhost:8081" == split.netloc
        assert "/reset-error" == split.path
        qparams = dict(parse_qsl(split.query))
        assert len(qparams) == 1
        assert all(k in qparams for k in ["error"])

        msg = get_message("INVALID_RESET_PASSWORD_TOKEN")
        assert msg == qparams["error"].encode("utf-8")
    assert len(flashes) == 0


@pytest.mark.settings(password_complexity_checker="zxcvbn")
def test_easy_password(client, get_message):
    with capture_reset_password_requests() as requests:
        client.post("/reset", data=dict(email="joe@lp.com"), follow_redirects=True)

    token = requests[0]["token"]

    # use the token
    response = client.post(
        "/reset/" + token,
        data={"password": "mypassword", "password_confirm": "mypassword"},
        follow_redirects=True,
    )

    assert b"This is a very common password" in response.data


def test_reset_inactive(client, get_message):
    response = client.post(
        "/reset", data=dict(email="tiya@lp.com"), follow_redirects=True
    )
    assert get_message("DISABLED_ACCOUNT") in response.data

    response = client.post(
        "/reset",
        json=dict(email="tiya@lp.com"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_email_normalization(client, get_message):
    response = client.post(
        "/reset", data=dict(email="joe@LP.COM"), follow_redirects=True
    )
    assert response.status_code == 200
    assert get_message("PASSWORD_RESET_REQUEST", email="joe@lp.com") in response.data


def test_password_normalization(app, client, get_message):
    with capture_reset_password_requests() as requests:
        response = client.post(
            "/reset",
            json=dict(email="matt@lp.com"),
        )
        assert response.status_code == 200
    token = requests[0]["token"]

    response = client.post(
        "/reset/" + token,
        json=dict(password="HöheHöhe", password_confirm="HöheHöhe"),
    )
    assert response.status_code == 200
    logout(client)

    # make sure can log in with new password both normalized or not
    response = client.post(
        "/login",
        json=dict(email="matt@lp.com", password="HöheHöhe"),
    )
    assert response.status_code == 200
    # verify actually logged in
    response = client.get("/profile", follow_redirects=False)
    assert response.status_code == 200
    logout(client)

    response = client.post(
        "/login",
        json=dict(email="matt@lp.com", password="Ho\u0308heHo\u0308he"),
    )
    assert response.status_code == 200
    # verify actually logged in
    response = client.get("/profile", follow_redirects=False)
    assert response.status_code == 200


@pytest.mark.settings(return_generic_responses=True)
def test_generic_response(app, client, get_message):
    # try unknown user
    response = client.post("/reset", data=dict(email="whoami@test.com"))
    assert (
        get_message("PASSWORD_RESET_REQUEST", email="whoami@test.com") in response.data
    )

    response = client.post("/reset", json=dict(email="whoami@test.com"))
    assert response.status_code == 200
    assert not any(e in response.json["response"].keys() for e in ["error", "errors"])


def test_generic_with_extra(app, sqlalchemy_datastore):
    # If application adds a field, make sure we properly return errors
    # even if 'RETURN_GENERIC_RESPONSES' is set.
    class MyForgotPasswordForm(ForgotPasswordForm):
        recaptcha = StringField("Recaptcha", validators=[Length(min=5)])

    app.config["SECURITY_RETURN_GENERIC_RESPONSES"] = True
    app.config["SECURITY_FORGOT_PASSWORD_TEMPLATE"] = "generic_reset.html"
    app.security = Security(
        app,
        datastore=sqlalchemy_datastore,
        forgot_password_form=MyForgotPasswordForm,
    )

    populate_data(app)
    client = app.test_client()

    # Test valid user but invalid additional form field
    # We should get a form error for the extra (invalid) field, no flash
    bad_data = dict(email="joe@lp.com", recaptcha="1234")
    good_data = dict(email="joe@lp.com", recaptcha="123456")

    with capture_flashes() as flashes:
        response = client.post("/reset", data=bad_data)
        assert b"Field must be at least 5" in response.data
    assert len(flashes) == 0
    with capture_flashes() as flashes:
        response = client.post("/reset", data=good_data)
    assert len(flashes) == 1

    # JSON
    with capture_flashes() as flashes:
        response = client.post("/reset", json=bad_data)
        assert response.status_code == 400
        assert (
            "Field must be at least 5"
            in response.json["response"]["field_errors"]["recaptcha"][0]
        )
    assert len(flashes) == 0
    with capture_flashes() as flashes:
        response = client.post("/reset", json=good_data)
        assert response.status_code == 200
    assert len(flashes) == 0

    # Try bad email AND bad recaptcha
    bad_data = dict(email="joe44-lp.com", recaptcha="1234")
    with capture_flashes() as flashes:
        response = client.post("/reset", data=bad_data)
        assert b"Field must be at least 5" in response.data
    assert len(flashes) == 0
    with capture_flashes() as flashes:
        response = client.post("/reset", json=bad_data)
        assert response.status_code == 400
        assert (
            "Field must be at least 5"
            in response.json["response"]["field_errors"]["recaptcha"][0]
        )
        assert len(response.json["response"]["errors"]) == 1
    assert len(flashes) == 0


@pytest.mark.filterwarnings("ignore")
@pytest.mark.settings(auto_login_after_reset=True, post_reset_view="/post_reset")
def test_auto_login(client, get_message):
    # test backwards compat flag (not OWASP recommended)
    with capture_reset_password_requests() as requests:
        response = client.post(
            "/reset", data=dict(email="joe@lp.com"), follow_redirects=True
        )
    assert response.status_code == 200
    token = requests[0]["token"]

    # Test submitting a new password
    with capture_flashes() as flashes:
        response = client.post(
            "/reset/" + token,
            data=dict(password="awesome sunset", password_confirm="awesome sunset"),
            follow_redirects=True,
        )
        assert b"Post Reset" in response.data
    assert len(flashes) == 1
    assert get_message("PASSWORD_RESET") == flashes[0]["message"].encode("utf-8")

    # verify actually logged in
    response = client.get("/profile", follow_redirects=False)
    assert response.status_code == 200


@pytest.mark.filterwarnings("ignore")
@pytest.mark.settings(auto_login_after_reset=True)
def test_auto_login_json(client, get_message):
    # test backwards compat flag (not OWASP recommended)
    with capture_reset_password_requests() as requests:
        response = client.post(
            "/reset",
            json=dict(email="joe@lp.com"),
        )
        assert response.headers["Content-Type"] == "application/json"

    assert response.status_code == 200
    token = requests[0]["token"]

    # Test submitting a new password
    response = client.post(
        "/reset/" + token + "?include_auth_token",
        json=dict(password="awesome sunset", password_confirm="awesome sunset"),
    )
    assert all(
        k in response.json["response"]["user"]
        for k in ["email", "authentication_token"]
    )
    # verify actually logged in
    response = client.get("/profile", follow_redirects=False)
    assert response.status_code == 200


@pytest.mark.flask_async()
@pytest.mark.settings()
def test_recoverable_json_async(app, client, get_message):
    recorded_resets = []
    recorded_instructions_sent = []

    @password_reset.connect_via(app)
    async def on_password_reset(myapp, user):
        recorded_resets.append(user)

    @reset_password_instructions_sent.connect_via(app)
    async def on_instructions_sent(myapp, **kwargs):
        recorded_instructions_sent.append(kwargs["user"])

    # Test reset password creates a token and sends email
    with capture_reset_password_requests() as requests:
        response = client.post(
            "/reset",
            json=dict(email="joe@lp.com"),
            headers={"Content-Type": "application/json"},
        )

    assert len(recorded_instructions_sent) == 1
    assert response.status_code == 200
    token = requests[0]["token"]

    # Test submitting a new password
    response = client.post(
        "/reset/" + token + "?include_auth_token",
        json=dict(password="awesome sunset", password_confirm="awesome sunset"),
    )
    assert not response.json["response"]
    assert len(recorded_resets) == 1


@pytest.mark.csrf()
@pytest.mark.settings(post_reset_view="/post_reset_view")
def test_csrf(app, client, get_message):
    response = client.get("/reset")
    csrf_token = get_form_input_value(response, "csrf_token")
    with capture_reset_password_requests() as requests:
        client.post(
            "/reset",
            data=dict(email="joe@lp.com", csrf_token=csrf_token),
            follow_redirects=True,
        )
    token = requests[0]["token"]

    # use the token - no CSRF so shouldn't work
    data = {"password": "mypassword", "password_confirm": "mypassword"}
    response = client.post(
        "/reset/" + token,
        data=data,
    )
    assert b"The CSRF token is missing" in response.data

    data["csrf_token"] = csrf_token
    response = client.post(f"/reset/{token}", data=data)
    assert check_location(app, response.location, "/post_reset_view")


@pytest.mark.username_recovery()
def test_username_recovery_valid_email(app, clients, get_message):
    recorded_recovery_sent = []

    @username_recovery_email_sent.connect_via(app)
    def on_email_sent(app, **kwargs):
        assert isinstance(app, Flask)
        assert isinstance(kwargs["user"], UserMixin)
        recorded_recovery_sent.append(kwargs["user"])

    # Test the username recovery view
    response = clients.get("/recover-username")
    assert b"<h1>Username Recovery</h1>" in response.data

    response = clients.post(
        "/recover-username", data=dict(email="joe@lp.com"), follow_redirects=True
    )

    assert len(recorded_recovery_sent) == 1
    assert len(app.mail.outbox) == 1
    assert response.status_code == 200

    with capture_flashes() as flashes:
        response = clients.post(
            "/recover-username",
            data=dict(email="joe@lp.com"),
            follow_redirects=True,
        )
    assert len(flashes) == 1
    assert get_message("USERNAME_RECOVERY_REQUEST") == flashes[0]["message"].encode(
        "utf-8"
    )

    # Validate the emailed username
    email = app.mail.outbox[1]
    assert "Your username is: joe" in email.body

    # Test JSON responses
    response = clients.post(
        "/recover-username",
        json=dict(email="joe@lp.com"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"


@pytest.mark.username_recovery()
def test_username_recovery_invalid_email(app, clients):
    response = clients.post(
        "/recover-username", data=dict(email="bogus@lp.com"), follow_redirects=True
    )

    assert not app.mail.outbox
    assert response.status_code == 200

    # Test JSON responses
    response = clients.post(
        "/recover-username",
        json=dict(email="bogus@lp.com"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.headers["Content-Type"] == "application/json"
    assert len(response.json["response"]["errors"]) == 1
    assert (
        "Specified user does not exist"
        in response.json["response"]["field_errors"]["email"][0]
    )


@pytest.mark.username_recovery()
@pytest.mark.settings(return_generic_responses=True)
def test_username_recovery_generic_responses(app, clients, get_message):
    recorded_recovery_sent = []

    @username_recovery_email_sent.connect_via(app)
    def on_email_sent(app, **kwargs):
        recorded_recovery_sent.append(kwargs["user"])

    # Test with valid email
    with capture_flashes() as flashes:
        response = clients.post(
            "/recover-username",
            data=dict(email="joe@lp.com"),
            follow_redirects=True,
        )
    assert len(flashes) == 1
    assert get_message("USERNAME_RECOVERY_REQUEST") == flashes[0]["message"].encode(
        "utf-8"
    )
    assert len(recorded_recovery_sent) == 1
    assert len(app.mail.outbox) == 1
    assert response.status_code == 200

    # Test with non-existant email (should still return 200)
    with capture_flashes() as flashes:
        response = clients.post(
            "/recover-username",
            data=dict(email="bogus@lp.com"),
            follow_redirects=True,
        )
    assert len(flashes) == 1
    assert get_message("USERNAME_RECOVERY_REQUEST") == flashes[0]["message"].encode(
        "utf-8"
    )
    # Validate no email was sent (there should only be one from the previous test)
    assert len(recorded_recovery_sent) == 1
    assert len(app.mail.outbox) == 1
    assert response.status_code == 200

    # Test JSON responses - valid email
    response = clients.post(
        "/recover-username",
        json=dict(email="joe@lp.com"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"

    # Test JSON responses - invalid email
    response = clients.post(
        "/recover-username",
        json=dict(email="bogus@lp.com"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert not any(e in response.json["response"].keys() for e in ["error", "errors"])
