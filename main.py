import mimetypes
import re
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from i18n import translate
from models import Activity, Document, User, now_utc
from services import (
    checksum_file,
    compress_pdf,
    extension_of,
    extract_document_text,
    extract_pdf_pages,
    image_to_pdf,
    images_to_pdf,
    merge_pdfs,
    pdf_page_count,
    rotate_pdf,
    split_pdf,
    unique_name,
    validate_document,
    watermark_pdf,
)


main = Blueprint("main", __name__)
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def t(key):
    return translate(current_user.language if current_user.is_authenticated else "ru", key)


def log(action, details="", user_id=None):
    db.session.add(
        Activity(
            action=action,
            details=details[:500],
            user_id=user_id or current_user.id,
            ip_address=(request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip())[:45],
            user_agent=str(request.user_agent)[:255],
        )
    )


def active_documents(user_id=None):
    query = Document.query.filter(Document.deleted_at.is_(None))
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    return query


def owned(document_id, include_deleted=False):
    query = Document.query.filter_by(id=document_id)
    if current_user.role != "admin":
        query = query.filter_by(user_id=current_user.id)
    if not include_deleted:
        query = query.filter(Document.deleted_at.is_(None))
    return query.first() or abort(404)


def document_path(document):
    path = (Path(current_app.config["UPLOAD_FOLDER"]) / document.stored_name).resolve()
    upload_root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    if path.parent != upload_root:
        abort(404)
    return path


def storage_used(user_id):
    return int(
        active_documents(user_id)
        .with_entities(func.coalesce(func.sum(Document.size), 0))
        .scalar()
        or 0
    )


def ensure_quota(extra_size):
    if storage_used(current_user.id) + int(extra_size) > current_user.storage_quota:
        raise ValueError("Недостаточно места. Освободите хранилище или обратитесь к администратору.")


def save_record(path, original_name, action="upload", details=None):
    ensure_quota(path.stat().st_size)
    extension = extension_of(original_name)
    doc = Document(
        original_name=original_name[:255],
        stored_name=path.name,
        mime_type=mimetypes.guess_type(original_name)[0] or "application/octet-stream",
        size=path.stat().st_size,
        extracted_text=extract_document_text(path),
        page_count=pdf_page_count(path) if extension == "pdf" else None,
        checksum=checksum_file(path),
        user_id=current_user.id,
    )
    db.session.add(doc)
    log(action, details or original_name)
    db.session.commit()
    return doc


def format_size(size):
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def requested_documents(field="document_ids"):
    values = request.form.getlist(field)
    if not values and request.form.get("document_id"):
        values = [request.form["document_id"]]
    if len(values) > 40:
        raise ValueError("За одну операцию можно выбрать не больше 40 файлов")
    return [owned(int(value)) for value in values]


@main.get("/")
@login_required
def dashboard():
    q = request.args.get("q", "").strip()[:200]
    kind = request.args.get("kind", "all")
    scope = request.args.get("scope", "all")
    folder = request.args.get("folder", "").strip()[:120]
    query = active_documents() if current_user.role == "admin" else active_documents(current_user.id)
    if q:
        escaped = q.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        query = query.filter(
            or_(
                Document.original_name.ilike(pattern, escape="\\"),
                Document.extracted_text.ilike(pattern, escape="\\"),
                Document.folder.ilike(pattern, escape="\\"),
            )
        )
    if kind in {"pdf", "docx", "image"}:
        if kind == "image":
            query = query.filter(
                or_(
                    Document.original_name.ilike("%.png"),
                    Document.original_name.ilike("%.jpg"),
                    Document.original_name.ilike("%.jpeg"),
                )
            )
        else:
            query = query.filter(Document.original_name.ilike(f"%.{kind}"))
    if scope == "favorites":
        query = query.filter_by(is_favorite=True)
    if folder:
        query = query.filter_by(folder=folder)
    documents = query.order_by(Document.created_at.desc()).limit(300).all()
    own_query = active_documents(current_user.id)
    used = storage_used(current_user.id)
    stats = {
        "count": own_query.count(),
        "pdf": own_query.filter(Document.original_name.ilike("%.pdf")).count(),
        "size": format_size(used),
        "quota": format_size(current_user.storage_quota),
        "percent": min(round((used / current_user.storage_quota) * 100), 100) if current_user.storage_quota else 100,
    }
    return render_template(
        "dashboard.html",
        documents=documents,
        q=q,
        kind=kind,
        scope=scope,
        folder=folder,
        folders=[row[0] for row in active_documents(current_user.id).with_entities(Document.folder).filter(Document.folder != "").distinct().order_by(Document.folder).all()],
        stats=stats,
        trash_count=Document.query.filter_by(user_id=current_user.id).filter(Document.deleted_at.is_not(None)).count(),
        format_size=format_size,
    )


@main.post("/upload")
@login_required
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash(t("choose_file"), "warning")
        return redirect(url_for("main.dashboard"))
    original_name = Path(file.filename.replace("\\", "/")).name.strip()[:255]
    extension = extension_of(original_name)
    if extension not in current_app.config["ALLOWED_EXTENSIONS"]:
        flash(t("unsupported"), "danger")
        return redirect(url_for("main.dashboard"))
    path = Path(current_app.config["UPLOAD_FOLDER"]) / unique_name(original_name)
    try:
        file.save(path)
        validate_document(path, extension)
        ensure_quota(path.stat().st_size)
        checksum = checksum_file(path)
        duplicate = active_documents(current_user.id).filter_by(checksum=checksum).first()
        if duplicate:
            path.unlink(missing_ok=True)
            flash(f"Такой файл уже загружен: {duplicate.original_name}", "warning")
            return redirect(url_for("main.dashboard"))
        save_record(path, original_name)
    except ValueError as error:
        path.unlink(missing_ok=True)
        db.session.rollback()
        flash(str(error), "danger")
        return redirect(url_for("main.dashboard"))
    except Exception:
        path.unlink(missing_ok=True)
        db.session.rollback()
        raise
    flash(t("document_uploaded"), "success")
    return redirect(url_for("main.dashboard"))


@main.get("/documents/<int:document_id>/download")
@login_required
def download(document_id):
    doc = owned(document_id, include_deleted=current_user.role == "admin")
    path = document_path(doc)
    if not path.is_file():
        abort(404)
    log("download", doc.original_name)
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=doc.original_name, mimetype=doc.mime_type)


@main.get("/documents/<int:document_id>/preview")
@login_required
def preview(document_id):
    doc = owned(document_id, include_deleted=current_user.role == "admin")
    if extension_of(doc.original_name) != "pdf":
        abort(404)
    path = document_path(doc)
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=False, download_name=doc.original_name, mimetype="application/pdf")


@main.post("/documents/<int:document_id>/favorite")
@login_required
def favorite(document_id):
    doc = owned(document_id)
    doc.is_favorite = not doc.is_favorite
    log("favorite" if doc.is_favorite else "unfavorite", doc.original_name)
    db.session.commit()
    return redirect(request.referrer or url_for("main.dashboard"))


@main.post("/documents/<int:document_id>/update")
@login_required
def update_document(document_id):
    doc = owned(document_id)
    name = request.form.get("name", "").strip()[:240]
    folder = request.form.get("folder", "").strip()[:120]
    tags = ", ".join(tag.strip() for tag in request.form.get("tags", "").split(",") if tag.strip())[:300]
    if not name:
        flash("Название не может быть пустым", "danger")
    else:
        original_extension = Path(doc.original_name).suffix
        if Path(name).suffix.lower() != original_extension.lower():
            name = f"{Path(name).stem}{original_extension}"
        doc.original_name = name
        doc.folder = folder
        doc.tags = tags
        log("update", name)
        db.session.commit()
        flash("Документ обновлён", "success")
    return redirect(url_for("main.dashboard"))


@main.post("/documents/bulk")
@login_required
def bulk_documents():
    ids = request.form.getlist("document_ids")
    if not ids or len(ids) > 100:
        flash("Выберите от 1 до 100 документов", "warning")
        return redirect(url_for("main.dashboard"))
    documents = [owned(int(document_id)) for document_id in ids]
    action = request.form.get("action", "")
    if action == "favorite":
        for document in documents:
            document.is_favorite = True
        message = "Документы добавлены в избранное"
    elif action == "delete":
        for document in documents:
            document.deleted_at = now_utc()
        message = "Документы перемещены в корзину"
    elif action == "folder":
        folder = request.form.get("folder", "").strip()[:120]
        for document in documents:
            document.folder = folder
        message = "Папка обновлена"
    else:
        abort(400)
    log(f"bulk_{action}", f"{len(documents)} документов")
    db.session.commit()
    flash(message, "success")
    return redirect(url_for("main.dashboard"))


@main.post("/documents/<int:document_id>/delete")
@login_required
def delete(document_id):
    doc = owned(document_id)
    doc.deleted_at = now_utc()
    log("delete", doc.original_name)
    db.session.commit()
    flash("Документ перемещён в корзину", "success")
    return redirect(url_for("main.dashboard"))


@main.get("/trash")
@login_required
def trash():
    documents = (
        Document.query.filter_by(user_id=current_user.id)
        .filter(Document.deleted_at.is_not(None))
        .order_by(Document.deleted_at.desc())
        .all()
    )
    return render_template("trash.html", documents=documents, format_size=format_size)


@main.post("/documents/<int:document_id>/restore")
@login_required
def restore(document_id):
    doc = owned(document_id, include_deleted=True)
    if doc.user_id != current_user.id and current_user.role != "admin":
        abort(404)
    doc.deleted_at = None
    log("restore", doc.original_name)
    db.session.commit()
    flash("Документ восстановлен", "success")
    return redirect(url_for("main.trash"))


@main.post("/documents/<int:document_id>/purge")
@login_required
def purge(document_id):
    doc = owned(document_id, include_deleted=True)
    if doc.deleted_at is None:
        abort(400)
    path = document_path(doc)
    name = doc.original_name
    db.session.delete(doc)
    log("purge", name)
    db.session.commit()
    path.unlink(missing_ok=True)
    flash("Документ удалён безвозвратно", "success")
    return redirect(url_for("main.trash"))


@main.route("/tools", methods=["GET", "POST"])
@login_required
def tools():
    mode = request.args.get("mode", "merge")
    if request.method == "POST":
        action = request.form.get("action", "")
        output = None
        try:
            docs = requested_documents()
            if action == "merge":
                if not 2 <= len(docs) <= 20:
                    raise ValueError("Выберите от 2 до 20 PDF")
                if any(extension_of(doc.original_name) != "pdf" for doc in docs):
                    raise ValueError("Для объединения доступны только PDF")
                output = Path(current_app.config["UPLOAD_FOLDER"]) / unique_name("merged.pdf")
                merge_pdfs([document_path(doc) for doc in docs], output)
                result_name = request.form.get("output_name", "merged").strip()[:180] or "merged"
                save_record(output, f"{Path(result_name).stem}.pdf", "merge", ", ".join(doc.original_name for doc in docs))
            elif action == "split":
                if len(docs) != 1 or extension_of(docs[0].original_name) != "pdf":
                    raise ValueError("Выберите один PDF")
                doc = docs[0]
                output = Path(current_app.config["UPLOAD_FOLDER"]) / unique_name("pages.pdf")
                ranges = request.form.get("ranges", "").strip()
                if ranges:
                    extract_pdf_pages(document_path(doc), ranges, output)
                    suffix = re.sub(r"[^0-9,-]", "", ranges).replace(",", "_")[:40]
                else:
                    start = int(request.form.get("start", "0"))
                    end = int(request.form.get("end", "0"))
                    split_pdf(document_path(doc), start, end, output)
                    suffix = f"{start}-{end}"
                save_record(output, f"{Path(doc.original_name).stem}_pages_{suffix}.pdf", "split", doc.original_name)
            elif action == "image":
                if not docs or any(extension_of(doc.original_name) not in {"png", "jpg", "jpeg"} for doc in docs):
                    raise ValueError("Выберите PNG или JPG")
                output = Path(current_app.config["UPLOAD_FOLDER"]) / unique_name("images.pdf")
                if len(docs) == 1 and request.form.get("page_size", "original") == "original":
                    image_to_pdf(document_path(docs[0]), output)
                else:
                    images_to_pdf([document_path(doc) for doc in docs], output, request.form.get("page_size", "original"))
                default_name = Path(docs[0].original_name).stem if len(docs) == 1 else "images"
                result_name = request.form.get("output_name", default_name).strip()[:180] or default_name
                save_record(output, f"{Path(result_name).stem}.pdf", "image", ", ".join(doc.original_name for doc in docs))
            elif action == "rotate":
                if len(docs) != 1 or extension_of(docs[0].original_name) != "pdf":
                    raise ValueError("Выберите один PDF")
                doc = docs[0]
                angle = int(request.form.get("angle", "90"))
                output = Path(current_app.config["UPLOAD_FOLDER"]) / unique_name("rotated.pdf")
                rotate_pdf(document_path(doc), angle, output)
                save_record(output, f"{Path(doc.original_name).stem}_rotated_{angle}.pdf", "rotate", doc.original_name)
            elif action == "compress":
                if len(docs) != 1 or extension_of(docs[0].original_name) != "pdf":
                    raise ValueError("Выберите один PDF")
                doc = docs[0]
                output = Path(current_app.config["UPLOAD_FOLDER"]) / unique_name("compressed.pdf")
                compress_pdf(document_path(doc), output)
                save_record(output, f"{Path(doc.original_name).stem}_compressed.pdf", "compress", doc.original_name)
            elif action == "watermark":
                if len(docs) != 1 or extension_of(docs[0].original_name) != "pdf":
                    raise ValueError("Выберите один PDF")
                doc = docs[0]
                output = Path(current_app.config["UPLOAD_FOLDER"]) / unique_name("watermarked.pdf")
                watermark_pdf(
                    document_path(doc),
                    request.form.get("watermark_text", ""),
                    output,
                    request.form.get("opacity", "55"),
                    request.form.get("page_numbers") == "on",
                )
                save_record(output, f"{Path(doc.original_name).stem}_watermarked.pdf", "watermark", doc.original_name)
            else:
                raise ValueError("Неизвестная операция")
            flash(t("operation_done"), "success")
        except (ValueError, OSError, TypeError) as error:
            if output:
                output.unlink(missing_ok=True)
            db.session.rollback()
            flash(str(error), "danger")
        return redirect(url_for("main.tools", mode=action or mode))
    documents = active_documents(current_user.id).order_by(Document.created_at.desc()).all()
    return render_template("tools.html", documents=documents, mode=mode, format_size=format_size)


@main.get("/history")
@login_required
def history():
    action = request.args.get("action", "")[:30]
    query = Activity.query if current_user.role == "admin" else Activity.query.filter_by(user_id=current_user.id)
    if action:
        query = query.filter_by(action=action)
    activities = query.order_by(Activity.created_at.desc()).limit(300).all()
    return render_template("history.html", activities=activities, action=action)


@main.route("/profile", methods=["GET"])
@login_required
def profile():
    used = storage_used(current_user.id)
    return render_template(
        "profile.html",
        used=format_size(used),
        quota=format_size(current_user.storage_quota),
        percent=min(round((used / current_user.storage_quota) * 100), 100) if current_user.storage_quota else 100,
        document_count=active_documents(current_user.id).count(),
        activity_count=Activity.query.filter_by(user_id=current_user.id).count(),
    )


@main.post("/profile/details")
@login_required
def profile_details():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    language = request.form.get("language", "ru")
    if not 2 <= len(name) <= 120 or not EMAIL_PATTERN.match(email) or language not in {"ru", "kk", "en"}:
        flash(t("invalid_form"), "danger")
    elif User.query.filter(User.email == email, User.id != current_user.id).first():
        flash(t("user_exists"), "danger")
    else:
        current_user.name = name
        current_user.email = email
        current_user.language = language
        log("profile", "Профиль обновлён")
        db.session.commit()
        flash("Профиль сохранён", "success")
    return redirect(url_for("main.profile"))


@main.post("/profile/preferences")
@login_required
def profile_preferences():
    theme = request.form.get("theme", "system")
    current_user.theme = theme if theme in {"light", "dark", "system"} else "system"
    current_user.email_notifications = bool(request.form.get("email_notifications"))
    current_user.compact_mode = bool(request.form.get("compact_mode"))
    log("preferences", "Настройки интерфейса обновлены")
    db.session.commit()
    flash("Настройки сохранены", "success")
    return redirect(url_for("main.profile"))


@main.post("/profile/password")
@login_required
def profile_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirmation = request.form.get("confirm_password", "")
    if not check_password_hash(current_user.password_hash, current_password):
        flash("Текущий пароль указан неверно", "danger")
    elif len(new_password) < 8 or len(new_password) > 128 or new_password != confirmation:
        flash("Новый пароль должен содержать минимум 8 символов и совпадать с подтверждением", "danger")
    else:
        current_user.password_hash = generate_password_hash(new_password)
        log("password", "Пароль изменён")
        db.session.commit()
        flash("Пароль изменён", "success")
    return redirect(url_for("main.profile"))


@main.post("/language/<language>")
@login_required
def language(language):
    if language in {"ru", "kk", "en"}:
        current_user.language = language
        db.session.commit()
    return redirect(request.referrer or url_for("main.dashboard"))


def require_admin():
    if current_user.role != "admin":
        abort(403)


@main.get("/admin")
@login_required
def admin():
    require_admin()
    q = request.args.get("q", "").strip()[:120]
    query = User.query
    if q:
        query = query.filter(or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    users = query.order_by(User.created_at.desc()).all()
    user_rows = []
    for user in users:
        used = storage_used(user.id)
        user_rows.append(
            {
                "user": user,
                "documents": active_documents(user.id).count(),
                "storage": format_size(used),
                "percent": min(round((used / user.storage_quota) * 100), 100) if user.storage_quota else 100,
                "activities": Activity.query.filter_by(user_id=user.id).count(),
            }
        )
    today = now_utc().date()
    return render_template(
        "admin.html",
        user_rows=user_rows,
        q=q,
        document_count=active_documents().count(),
        storage=format_size(db.session.query(func.coalesce(func.sum(Document.size), 0)).filter(Document.deleted_at.is_(None)).scalar()),
        activity_count=Activity.query.count(),
        active_today=User.query.filter(func.date(User.last_login_at) == today).count(),
        recent_activities=Activity.query.order_by(Activity.created_at.desc()).limit(8).all(),
    )


@main.get("/admin/users/<int:user_id>")
@login_required
def admin_user(user_id):
    require_admin()
    user = db.get_or_404(User, user_id)
    action = request.args.get("action", "")[:30]
    activity_query = Activity.query.filter_by(user_id=user.id)
    if action:
        activity_query = activity_query.filter_by(action=action)
    documents = Document.query.filter_by(user_id=user.id).order_by(Document.created_at.desc()).all()
    used = storage_used(user.id)
    return render_template(
        "admin_user.html",
        selected_user=user,
        documents=documents,
        activities=activity_query.order_by(Activity.created_at.desc()).limit(300).all(),
        action=action,
        used=format_size(used),
        quota=format_size(user.storage_quota),
        percent=min(round((used / user.storage_quota) * 100), 100) if user.storage_quota else 100,
        format_size=format_size,
    )


@main.post("/admin/users/<int:user_id>/settings")
@login_required
def admin_user_settings(user_id):
    require_admin()
    user = db.get_or_404(User, user_id)
    role = request.form.get("role", "user")
    requested_active = bool(request.form.get("is_active"))
    try:
        quota_mb = int(request.form.get("storage_quota_mb", "512"))
    except ValueError:
        quota_mb = 512
    if not 10 <= quota_mb <= 10240:
        flash("Лимит должен быть от 10 до 10240 МБ", "danger")
        return redirect(url_for("main.admin_user", user_id=user.id))
    if user.id == current_user.id and (role != "admin" or not requested_active):
        flash("Нельзя понизить или заблокировать собственный аккаунт", "danger")
        return redirect(url_for("main.admin_user", user_id=user.id))
    if user.role == "admin" and role != "admin" and User.query.filter_by(role="admin", is_active=True).count() <= 1:
        flash("В системе должен остаться хотя бы один активный администратор", "danger")
        return redirect(url_for("main.admin_user", user_id=user.id))
    user.role = role if role in {"user", "admin"} else "user"
    user.is_active = requested_active
    user.storage_quota = quota_mb * 1024 * 1024
    log("admin_update", f"Администратор обновил роль, статус и лимит пользователя {user.email}")
    db.session.commit()
    flash("Настройки пользователя сохранены", "success")
    return redirect(url_for("main.admin_user", user_id=user.id))
