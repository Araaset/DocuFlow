import hashlib
import io
import re
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from werkzeug.utils import secure_filename


MAX_EXTRACTED_TEXT = 200_000
DOCX_TEXT_PATH = "word/document.xml"


def unique_name(filename):
    safe = secure_filename(filename) or "document"
    return f"{uuid.uuid4().hex}_{safe}"


def checksum_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extension_of(filename):
    return Path(filename).suffix.lower().lstrip(".")


def validate_document(path, extension):
    """Validate file contents, not just the user-controlled filename."""
    extension = extension.lower()
    try:
        if extension == "pdf":
            with path.open("rb") as source:
                if source.read(5) != b"%PDF-":
                    raise ValueError("Некорректный PDF-файл")
            reader = PdfReader(str(path), strict=False)
            if not reader.pages:
                raise ValueError("PDF не содержит страниц")
        elif extension in {"png", "jpg", "jpeg"}:
            with Image.open(path) as image:
                image.verify()
                actual = (image.format or "").lower()
                expected = "jpeg" if extension in {"jpg", "jpeg"} else "png"
                if actual != expected:
                    raise ValueError("Расширение изображения не соответствует содержимому")
        elif extension == "docx":
            with zipfile.ZipFile(path) as archive:
                if DOCX_TEXT_PATH not in archive.namelist():
                    raise ValueError("Некорректный DOCX-файл")
                if sum(item.file_size for item in archive.infolist()) > 200 * 1024 * 1024:
                    raise ValueError("Распакованный DOCX слишком большой")
        else:
            raise ValueError("Этот формат не поддерживается")
    except (OSError, zipfile.BadZipFile, UnidentifiedImageError) as error:
        raise ValueError("Файл повреждён или имеет неверный формат") from error
    except Exception as error:
        if isinstance(error, ValueError):
            raise
        raise ValueError("Файл повреждён или защищён паролем") from error


def extract_pdf_text(path):
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path), strict=False).pages)
        return text[:MAX_EXTRACTED_TEXT]
    except Exception:
        return ""


def extract_docx_text(path):
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read(DOCX_TEXT_PATH))
        fragments = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
        return re.sub(r"\s+", " ", " ".join(fragments)).strip()[:MAX_EXTRACTED_TEXT]
    except Exception:
        return ""


def extract_document_text(path):
    extension = path.suffix.lower()
    if extension == ".pdf":
        return extract_pdf_text(path)
    if extension == ".docx":
        return extract_docx_text(path)
    return ""


def pdf_page_count(path):
    try:
        return len(PdfReader(str(path), strict=False).pages)
    except Exception:
        return None


def merge_pdfs(paths, output_path):
    writer = PdfWriter()
    for path in paths:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise ValueError("Один из PDF защищён паролем")
        for page in reader.pages:
            writer.add_page(page)
    if not writer.pages:
        raise ValueError("Не удалось найти страницы для объединения")
    with output_path.open("wb") as target:
        writer.write(target)


def split_pdf(path, start_page, end_page, output_path):
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        raise ValueError("PDF защищён паролем")
    if start_page < 1 or end_page > len(reader.pages) or start_page > end_page:
        raise ValueError(f"Укажите диапазон от 1 до {len(reader.pages)}")
    writer = PdfWriter()
    for index in range(start_page - 1, end_page):
        writer.add_page(reader.pages[index])
    with output_path.open("wb") as target:
        writer.write(target)


def parse_page_ranges(value, page_count):
    pages = []
    for part in (item.strip() for item in value.split(",")):
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        if start < 1 or end > page_count or start > end:
            raise ValueError(f"Допустимые страницы: от 1 до {page_count}")
        pages.extend(range(start - 1, end))
    if not pages:
        raise ValueError("Укажите страницы, например: 1-3, 5, 8-10")
    if len(pages) != len(set(pages)):
        raise ValueError("Диапазоны страниц пересекаются")
    return pages


def extract_pdf_pages(path, ranges, output_path):
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        raise ValueError("PDF защищён паролем")
    writer = PdfWriter()
    for index in parse_page_ranges(ranges, len(reader.pages)):
        writer.add_page(reader.pages[index])
    with output_path.open("wb") as target:
        writer.write(target)


def rotate_pdf(path, angle, output_path):
    if angle not in {90, 180, 270}:
        raise ValueError("Выберите поворот 90°, 180° или 270°")
    reader = PdfReader(str(path), strict=False)
    writer = PdfWriter()
    for page in reader.pages:
        page.rotate(angle)
        writer.add_page(page)
    with output_path.open("wb") as target:
        writer.write(target)


def compress_pdf(path, output_path):
    reader = PdfReader(str(path), strict=False)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page).compress_content_streams()
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    with output_path.open("wb") as target:
        writer.write(target)


def _watermark_image(text, width, height, opacity):
    """Render Unicode text as an image so Cyrillic/Kazakh labels work everywhere."""
    scale = 2
    image = Image.new("RGBA", (max(int(width * scale), 1), max(int(height * scale), 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_size = max(30, min(int(min(width, height) * 0.085 * scale), 150))
    font_paths = (
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    )
    font = None
    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    text_layer = Image.new("RGBA", (max(box[2] - box[0] + 40, 1), max(box[3] - box[1] + 40, 1)), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_draw.text((20 - box[0], 20 - box[1]), text, font=font, fill=(91, 72, 210, opacity))
    text_layer = text_layer.rotate(32, expand=True, resample=Image.Resampling.BICUBIC)
    image.alpha_composite(text_layer, ((image.width - text_layer.width) // 2, (image.height - text_layer.height) // 2))
    return image


def watermark_pdf(path, text, output_path, opacity=55, page_numbers=False):
    text = (text or "").strip()[:80]
    if not text and not page_numbers:
        raise ValueError("Введите текст водяного знака или включите нумерацию")
    opacity = max(20, min(int(opacity), 180))
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        raise ValueError("PDF защищён паролем")
    writer = PdfWriter()
    total = len(reader.pages)
    for number, page in enumerate(reader.pages, 1):
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        overlay_stream = io.BytesIO()
        overlay = canvas.Canvas(overlay_stream, pagesize=(width, height))
        if text:
            watermark = _watermark_image(text, width, height, opacity)
            png_stream = io.BytesIO()
            watermark.save(png_stream, "PNG", optimize=True)
            png_stream.seek(0)
            overlay.drawImage(ImageReader(png_stream), 0, 0, width=width, height=height, mask="auto")
        if page_numbers:
            overlay.setFillColorRGB(0.35, 0.35, 0.4)
            overlay.setFont("Helvetica", 9)
            overlay.drawCentredString(width / 2, 18, f"{number} / {total}")
        overlay.save()
        overlay_stream.seek(0)
        writer.add_page(page).merge_page(PdfReader(overlay_stream).pages[0])
    with output_path.open("wb") as target:
        writer.write(target)


def image_to_pdf(path, output_path):
    try:
        with Image.open(path) as source:
            source.load()
            if source.mode in ("RGBA", "LA"):
                image = Image.new("RGB", source.size, "white")
                image.paste(source, mask=source.getchannel("A"))
            else:
                image = source.convert("RGB")
            image.save(output_path, "PDF", resolution=144.0, optimize=True)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("Не удалось прочитать изображение") from error


def images_to_pdf(paths, output_path, page_size="original"):
    images = []
    try:
        for path in paths:
            with Image.open(path) as source:
                source.load()
                if source.mode in ("RGBA", "LA"):
                    converted = Image.new("RGB", source.size, "white")
                    converted.paste(source, mask=source.getchannel("A"))
                else:
                    converted = source.convert("RGB")
                if page_size == "a4":
                    canvas = Image.new("RGB", (1240, 1754), "white")
                    converted.thumbnail((1120, 1634), Image.Resampling.LANCZOS)
                    canvas.paste(converted, ((1240 - converted.width) // 2, (1754 - converted.height) // 2))
                    converted = canvas
                images.append(converted)
        if not images:
            raise ValueError("Выберите хотя бы одно изображение")
        images[0].save(output_path, "PDF", save_all=True, append_images=images[1:], resolution=150.0)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("Не удалось прочитать одно из изображений") from error
    finally:
        for image in images:
            image.close()
