"""One-click Windows launcher for local DocuFlow installations."""

import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MARKER = VENV / ".docuflow-requirements"
URL = "http://127.0.0.1:5000"


def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def requirement_hash():
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def run_checked(command, message):
    print(f"\n{message}...")
    subprocess.run(command, cwd=ROOT, check=True)


def ensure_environment():
    python = venv_python()
    if not python.exists():
        run_checked([sys.executable, "-m", "venv", str(VENV)], "Создаю окружение DocuFlow")
    current_hash = requirement_hash()
    installed_hash = MARKER.read_text(encoding="utf-8").strip() if MARKER.exists() else ""
    if current_hash != installed_hash:
        run_checked(
            [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
            "Устанавливаю необходимые компоненты",
        )
        MARKER.write_text(current_hash, encoding="utf-8")
    return python


def server_status():
    try:
        with urllib.request.urlopen(URL, timeout=0.7) as response:
            return "docuflow" if b"DocuFlow" in response.read(20_000) else "occupied"
    except OSError:
        try:
            with socket.create_connection(("127.0.0.1", 5000), timeout=0.35):
                return "occupied"
        except OSError:
            return "free"


def open_when_ready():
    for _ in range(60):
        try:
            with urllib.request.urlopen(URL, timeout=0.4):
                webbrowser.open(URL)
                return
        except Exception:
            time.sleep(0.25)
    webbrowser.open(URL)


def main():
    os.chdir(ROOT)
    if "--check" in sys.argv:
        if not REQUIREMENTS.exists() or not (ROOT / "app.py").exists():
            print("Файлы DocuFlow неполные.")
            return 1
        print("Однокликовый запуск готов.")
        return 0
    print("=" * 54)
    print(" DocuFlow — запуск в один клик")
    print(" Закройте это окно, чтобы остановить локальный сайт.")
    print("=" * 54)
    try:
        python = ensure_environment()
        status = server_status()
        if status == "docuflow":
            print("\nDocuFlow уже запущен. Открываю сайт...")
            webbrowser.open(URL)
            return 0
        if status == "occupied":
            print("\nПорт 5000 занят другой программой. Закройте её и повторите запуск.")
            input("Нажмите Enter, чтобы закрыть окно...")
            return 1
        threading.Thread(target=open_when_ready, daemon=True).start()
        print(f"\nСайт открывается: {URL}\n")
        return subprocess.call([str(python), str(ROOT / "app.py")], cwd=ROOT)
    except KeyboardInterrupt:
        print("\nDocuFlow остановлен.")
        return 0
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"\nНе удалось запустить DocuFlow: {error}")
        print("Проверьте подключение к интернету и установку Python 3.11+.")
        input("Нажмите Enter, чтобы закрыть окно...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
