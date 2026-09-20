import hmac
import os
import secrets

from flask import Flask, abort, redirect, render_template, request, session, url_for
from flask_login import current_user, logout_user
from sqlalchemy import inspect, text
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import generate_password_hash

from config import Config
from extensions import db, login_manager
from i18n import translate
from models import User


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.session_protection = "strong"

    from auth import auth
    from main import main

    app.register_blueprint(auth)
    app.register_blueprint(main)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    def current_language():
        if current_user.is_authenticated:
            return current_user.language
        return session.get("language", "ru")

    def csrf_token():
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token

    @app.before_request
    def enforce_active_account():
        if current_user.is_authenticated and not current_user.is_active:
            logout_user()
            return redirect(url_for("auth.login"))
        return None

    @app.before_request
    def protect_csrf():
        if not app.config.get("CSRF_ENABLED", True) or request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            return None
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("_csrf_token", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            abort(400, translate(current_language(), "csrf_error"))
        return None

    @app.context_processor
    def template_helpers():
        language = current_language()
        return {
            "t": lambda key, **values: translate(language, key, **values),
            "csrf_token": csrf_token,
            "active_language": language,
        }

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-src 'self'",
        )
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return render_template("error.html", code=413, message=translate(current_language(), "too_large")), 413

    @app.errorhandler(400)
    def bad_request(error):
        message = getattr(error, "description", None) or translate(current_language(), "invalid_form")
        return render_template("error.html", code=400, message=message), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message=translate(current_language(), "not_found")), 404

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", code=403, message="Недостаточно прав для просмотра этой страницы"), 403

    @app.errorhandler(500)
    def server_error(_error):
        db.session.rollback()
        return render_template("error.html", code=500, message=translate(current_language(), "server_error")), 500

    @app.cli.command("init-db")
    def init_db():
        db.create_all()
        email, password = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
        if email and password and not User.query.filter_by(email=email.lower()).first():
            db.session.add(
                User(
                    name="Administrator",
                    email=email.lower(),
                    password_hash=generate_password_hash(password),
                    role="admin",
                )
            )
            db.session.commit()
        print("Database initialized")

    if app.config.get("AUTO_CREATE_DB", True):
        with app.app_context():
            db.create_all()
            _upgrade_sqlite_schema()

    return app


def _upgrade_sqlite_schema():
    """Keep existing local databases usable without a separate migration tool."""
    if db.engine.dialect.name != "sqlite":
        return
    columns = {
        "user": {
            "is_active": "BOOLEAN NOT NULL DEFAULT 1",
            "theme": "VARCHAR(12) NOT NULL DEFAULT 'system'",
            "email_notifications": "BOOLEAN NOT NULL DEFAULT 1",
            "compact_mode": "BOOLEAN NOT NULL DEFAULT 0",
            "storage_quota": "BIGINT NOT NULL DEFAULT 536870912",
            "last_login_at": "DATETIME",
            "updated_at": "DATETIME",
        },
        "document": {
            "folder": "VARCHAR(120) NOT NULL DEFAULT ''",
            "tags": "VARCHAR(300) NOT NULL DEFAULT ''",
            "is_favorite": "BOOLEAN NOT NULL DEFAULT 0",
            "deleted_at": "DATETIME",
            "checksum": "VARCHAR(64)",
        },
        "activity": {
            "ip_address": "VARCHAR(45)",
            "user_agent": "VARCHAR(255)",
        },
    }
    schema = inspect(db.engine)
    with db.engine.begin() as connection:
        for table_name, additions in columns.items():
            if table_name not in schema.get_table_names():
                continue
            existing = {item["name"] for item in schema.get_columns(table_name)}
            for column_name, definition in additions.items():
                if column_name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {definition}'))


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
