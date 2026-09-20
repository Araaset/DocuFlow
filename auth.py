import re
from urllib.parse import urljoin, urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from i18n import translate
from models import User, now_utc


auth = Blueprint("auth", __name__)
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def t(key):
    language = current_user.language if current_user.is_authenticated else request.form.get("language", "ru")
    return translate(language, key)


def safe_next_url(target):
    if not target:
        return None
    base = urlparse(request.host_url)
    candidate = urlparse(urljoin(request.host_url, target))
    return target if candidate.scheme in {"http", "https"} and candidate.netloc == base.netloc else None


@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and not user.is_active:
            flash("Аккаунт заблокирован администратором", "danger")
        elif user and check_password_hash(user.password_hash, password):
            user.last_login_at = now_utc()
            db.session.commit()
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(safe_next_url(request.args.get("next")) or url_for("main.dashboard"))
        flash(t("bad_login"), "danger")
    return render_template("login.html")


@auth.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        language = request.form.get("language", "ru")
        if language not in {"ru", "kk", "en"}:
            language = "ru"
        if not 2 <= len(name) <= 120 or len(password) < 8 or len(password) > 128 or not EMAIL_PATTERN.match(email):
            flash(translate(language, "invalid_form"), "danger")
        elif User.query.filter_by(email=email).first():
            flash(translate(language, "user_exists"), "warning")
        else:
            user = User(
                name=name,
                email=email,
                password_hash=generate_password_hash(password),
                language=language,
            )
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("main.dashboard"))
    return render_template("register.html")


@auth.post("/logout")
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
