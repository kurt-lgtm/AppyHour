"""Password gate — single shared password via APP_PASSWORD env var."""
from __future__ import annotations

import os
import hmac
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

auth_bp = Blueprint("auth", __name__)


def _check_password(submitted: str) -> bool:
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        raise RuntimeError("APP_PASSWORD not configured")
    return hmac.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if _check_password(pw):
            session.permanent = True
            session["authed"] = True
            return redirect(request.args.get("next") or url_for("main.index"))
        flash("Wrong password.", "error")
    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
