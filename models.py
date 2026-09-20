from datetime import datetime, timezone
from flask_login import UserMixin
from extensions import db

def now_utc():
    return datetime.now(timezone.utc)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    language = db.Column(db.String(2), nullable=False, default="ru")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    theme = db.Column(db.String(12), nullable=False, default="system")
    email_notifications = db.Column(db.Boolean, nullable=False, default=True)
    compact_mode = db.Column(db.Boolean, nullable=False, default=False)
    storage_quota = db.Column(db.BigInteger, nullable=False, default=512 * 1024 * 1024)
    last_login_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc)
    updated_at = db.Column(db.DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    documents = db.relationship("Document", backref="owner", lazy=True, cascade="all, delete-orphan")

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), unique=True, nullable=False)
    mime_type = db.Column(db.String(100))
    size = db.Column(db.BigInteger, nullable=False, default=0)
    extracted_text = db.Column(db.Text)
    page_count = db.Column(db.Integer)
    folder = db.Column(db.String(120), nullable=False, default="")
    tags = db.Column(db.String(300), nullable=False, default="")
    is_favorite = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True))
    checksum = db.Column(db.String(64), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, index=True)

class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.String(500))
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, index=True)
    user = db.relationship("User")
