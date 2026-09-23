import json
import io
import os
import sqlite3
import sys
import subprocess
import threading
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


FROZEN = getattr(sys, "frozen", False)
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if FROZEN:
    DATA_ROOT = Path(os.getenv("LOCALAPPDATA", Path.home())) / "Kontur"
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
else:
    DATA_ROOT = ROOT
DB_PATH = Path(os.getenv("CONTROL_DB", DATA_ROOT / "control.db"))
HOST = os.getenv("CONTROL_HOST", "127.0.0.1")
PORT = int(os.getenv("CONTROL_PORT", "8080"))
SENSITIVE_ENABLED = os.getenv("SENSITIVE_MODULES_ENABLED", "false").lower() == "true"
OPEN_BROWSER = os.getenv("CONTROL_OPEN_BROWSER", "true" if FROZEN else "false").lower() == "true"

MODULES = [
    {"id": "reminders", "title": "Напоминания и оценка риска", "sensitivity": "Низкая", "enabled": True},
    {"id": "intake", "title": "Приём документов вне СЭД", "sensitivity": "Повышенная", "enabled": SENSITIVE_ENABLED},
    {"id": "territories", "title": "Свод документооборота терорганов", "sensitivity": "Повышенная", "enabled": SENSITIVE_ENABLED},
    {"id": "proofreading", "title": "Предпроверка текста", "sensitivity": "Повышенная", "enabled": SENSITIVE_ENABLED},
]


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    connection = sqlite3.connect(DB_PATH, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    return connection


def show_message(title, message, error=False):
    if sys.platform == "win32":
        import ctypes
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)


def browser_candidates():
    paths = []
    if sys.platform != "win32":
        return paths
    try:
        import winreg
        for executable in ("msedge.exe", "chrome.exe", "firefox.exe"):
            key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        paths.append(winreg.QueryValue(key, None))
                except OSError:
                    pass
    except ImportError:
        pass
    locations = [os.getenv("PROGRAMFILES(X86)"), os.getenv("PROGRAMFILES"), os.getenv("LOCALAPPDATA")]
    relative_paths = (
        r"Microsoft\Edge\Application\msedge.exe",
        r"Google\Chrome\Application\chrome.exe",
        r"Mozilla Firefox\firefox.exe",
    )
    for location in locations:
        if location:
            paths.extend(str(Path(location) / relative) for relative in relative_paths)
    return paths


def open_modern_browser(url):
    """Open only an explicitly detected modern browser, never Internet Explorer."""
    for candidate in dict.fromkeys(browser_candidates()):
        if candidate and Path(candidate).is_file():
            try:
                subprocess.Popen([candidate, url], close_fds=True)
                return True
            except OSError:
                continue
    show_message(
        "Контур",
        "Microsoft Edge, Google Chrome или Mozilla Firefox не найден.\n\n"
        f"Откройте установленный современный браузер и введите адрес:\n{url}",
        error=True,
    )
    return False


def init_db():
    with connect() as db:
        had_assignments = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'assignments'").fetchone() is not None
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_number TEXT NOT NULL UNIQUE,
                assignee TEXT NOT NULL,
                assignee_email TEXT NOT NULL DEFAULT '',
                department TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'В работе',
                source TEXT NOT NULL DEFAULT 'СЭД',
                is_demo INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL,
                level TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'Протокол',
                FOREIGN KEY (assignment_id) REFERENCES assignments(id)
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                unit TEXT NOT NULL,
                baseline REAL,
                current REAL,
                measured_at TEXT
            );
            """
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(assignments)")}
        if "assignee_email" not in columns:
            db.execute("ALTER TABLE assignments ADD COLUMN assignee_email TEXT NOT NULL DEFAULT ''")
        count = db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
        if count == 0 and not had_assignments:
            today = date.today()
            samples = [
                ("ДЕМО-2026-014", "А. В. Петрова", "Отдел общего образования", today + timedelta(days=1)),
                ("ДЕМО-2026-021", "И. С. Левченко", "Организационный отдел", today + timedelta(days=4)),
                ("ДЕМО-2026-027", "М. Н. Орлова", "Отдел воспитательной работы", today - timedelta(days=1)),
                ("ДЕМО-2026-033", "С. П. Кравцов", "Правовой отдел", today + timedelta(days=9)),
            ]
            db.executemany(
                "INSERT INTO assignments (doc_number, assignee, department, due_date, is_demo) VALUES (?, ?, ?, ?, 1)",
                [(number, person, unit, due.isoformat()) for number, person, unit, due in samples],
            )
        metric_count = db.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        if metric_count == 0:
            db.executemany(
                "INSERT INTO metrics (id, title, unit) VALUES (?, ?, ?)",
                [
                    ("registration_time", "Время до регистрации документа", "час."),
                    ("territory_coverage", "Охват документооборота тероргана", "%"),
                    ("errors_detected", "Ошибки, замеченные на предпроверке", "%"),
                    ("advance_reminders", "Документы с заблаговременным напоминанием", "%"),
                    ("school_visits", "Личные визиты для доставки документов", "визитов"),
                ],
            )


def risk_for(due_date, status):
    if status == "Исполнено":
        return {"code": "done", "label": "Исполнено", "days": None}
    days = (date.fromisoformat(due_date) - date.today()).days
    if days < 0:
        return {"code": "overdue", "label": "Срок нарушен", "days": days}
    if days <= 1:
        return {"code": "critical", "label": "Критический", "days": days}
    if days <= 3:
        return {"code": "high", "label": "Высокий", "days": days}
    if days <= 7:
        return {"code": "medium", "label": "Средний", "days": days}
    return {"code": "low", "label": "Низкий", "days": days}


def excel_value(cell, shared_strings):
    value = cell.find("{*}v")
    raw = "" if value is None else value.text or ""
    if cell.get("t") == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell.get("t") == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//{*}t"))
    return raw


def read_xlsx(content):
    """Read the first worksheet using the XLSX zip/XML format, without third-party packages."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        shared_strings = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = ["".join(text.text or "" for text in item.findall(".//{*}t")) for item in root.findall("{*}si")]
        worksheets = sorted(
            name for name in names
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if not worksheets:
            raise ValueError("В Excel-файле не найден первый лист")
        root = ET.fromstring(archive.read(worksheets[0]))
        rows = []
        for row in root.findall(".//{*}sheetData/{*}row"):
            cells = {}
            for cell in row.findall("{*}c"):
                reference = cell.get("r", "A1")
                letters = "".join(character for character in reference if character.isalpha())
                column = 0
                for character in letters:
                    column = column * 26 + ord(character.upper()) - 64
                cells[column] = excel_value(cell, shared_strings).strip()
            if cells:
                rows.append([cells.get(index, "") for index in range(1, max(cells) + 1)])
        if not rows:
            raise ValueError("Первый лист Excel пуст")
        return rows


def normalize_header(value):
    value = str(value).lower().replace("ё", "е").replace("_", " ").replace("\n", " ")
    for character in ".,:;()[]{}-–—/\\":
        value = value.replace(character, " ")
    return " ".join(value.split())


def infer_header_field(header):
    words = set(header.split())
    if "email" in words or "e-mail" in words or "почта" in words or "электронная" in words:
        return "assignee_email"
    if "статус" in words or "состояние" in words:
        return "status"
    if "срок" in words or ("дата" in words and ("исполнения" in words or "контроля" in words)):
        return "due_date"
    if "подразделение" in words or "подразделения" in words or "подразделением" in words:
        return "department"
    if "отдел" in words or "управление" in words or "департамент" in words or "организация" in words or "организации" in words or "терорган" in words:
        return "department"
    if "исполнитель" in words or "исполнителя" in words or "ответственный" in words or "ответственного" in words:
        return "assignee"
    number_markers = {"номер", "номера", "№", "рег", "регистрационный", "регистрационный№", "индекс"}
    is_sequence = header in ("№ п п", "№ пп", "п п", "пп", "номер по порядку")
    if words & number_markers and not is_sequence:
        return "doc_number"
    if "входящий" in words or "исходящий" in words or "вх" in words or "исх" in words:
        return "doc_number"
    return None


def normalize_excel_date(value):
    value = str(value).strip()
    if not value:
        raise ValueError("пустая дата")
    try:
        serial = float(value.replace(",", "."))
        if 1 <= serial <= 2958465:
            return (date(1899, 12, 30) + timedelta(days=int(serial))).isoformat()
    except ValueError:
        pass
    cleaned = value.split("T", 1)[0].split(" ", 1)[0]
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%y", "%d/%m/%y"):
        try:
            return datetime.strptime(cleaned, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError("неизвестный формат даты")


def parse_excel_rows(content, filename):
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise ValueError("Поддерживается формат .xlsx. Сохраните старый .xls как книгу Excel (.xlsx)")
    rows = read_xlsx(content)
    aliases = {
        "doc_number": ("номер документа", "номер", "документ", "регистрационный номер", "регистрационный №", "рег №", "рег. номер", "рег. №", "№ документа", "входящий номер", "вх. №", "исходящий номер", "исх. №", "индекс документа", "doc_number", "document number"),
        "assignee": ("исполнитель", "ответственный", "ответственный исполнитель", "фио исполнителя", "assignee"),
        "assignee_email": ("email", "e-mail", "электронная почта", "почта исполнителя", "email исполнителя", "адрес электронной почты", "assignee_email"),
        "department": ("подразделение", "структурное подразделение", "ответственное подразделение", "ответственное структурное подразделение", "подразделение исполнитель", "отдел исполнитель", "ответственный отдел", "наименование подразделения", "наименование отдела", "организация исполнитель", "территориальный орган", "терорган", "департамент", "отдел", "управление", "department"),
        "due_date": ("срок исполнения", "контрольный срок", "срок", "дата срока", "дата исполнения", "due_date", "due date"),
        "status": ("статус", "состояние", "status"),
    }
    required = ("doc_number", "assignee", "due_date")
    indexes = {}
    header_row = 0
    best_match = -1
    for row_index, candidate in enumerate(rows[:20]):
        headers = [normalize_header(item) for item in candidate]
        candidate_indexes = {}
        for field, names in aliases.items():
            normalized_names = [normalize_header(name) for name in names]
            for column, header in enumerate(headers):
                if header in normalized_names:
                    candidate_indexes[field] = column
                    break
        for column, header in enumerate(headers):
            field = infer_header_field(header)
            if field and field not in candidate_indexes and column not in candidate_indexes.values():
                candidate_indexes[field] = column
        match_count = sum(field in candidate_indexes for field in required)
        if match_count > best_match:
            indexes = candidate_indexes
            header_row = row_index
            best_match = match_count
        if match_count == len(required):
            break
    labels = {"doc_number": "Номер документа", "assignee": "Исполнитель", "due_date": "Срок исполнения"}
    missing = [labels[field] for field in required if field not in indexes]
    if missing:
        detected = ", ".join(str(value).strip() for value in rows[header_row] if str(value).strip())
        raise ValueError(
            "Не найдены обязательные колонки: " + ", ".join(missing)
            + (f"\n\nНайдены заголовки: {detected}" if detected else "")
        )
    result = []
    for row_number, row in enumerate(rows[header_row + 1:], header_row + 2):
        values = {field: (row[index] if index < len(row) else "").strip() for field, index in indexes.items()}
        if not any(values.values()):
            continue
        if not all(values.get(field) for field in required):
            raise ValueError(f"Строка {row_number}: заполните номер документа, исполнителя и срок")
        values["department"] = values.get("department") or "Не указано"
        if values.get("assignee_email"):
            from mailer import valid_email
            if not valid_email(values["assignee_email"]):
                raise ValueError(f"Строка {row_number}: некорректный email исполнителя")
        else:
            values["assignee_email"] = ""
        try:
            values["due_date"] = normalize_excel_date(values["due_date"])
        except ValueError:
            raise ValueError(f"Строка {row_number}: не удалось распознать срок «{values['due_date']}»")
        values["status"] = values.get("status") or "В работе"
        if values["status"] not in ("В работе", "Исполнено"):
            values["status"] = "В работе"
        result.append(values)
    return result


def assignment_dict(row):
    item = dict(row)
    item["is_demo"] = bool(item["is_demo"])
    item["risk"] = risk_for(item["due_date"], item["status"])
    return item


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "public"), **kwargs)

    def log_message(self, fmt, *args):
        if sys.stdout:
            print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            return self.dashboard()
        if path == "/api/assignments":
            return self.assignments()
        if path == "/api/metrics":
            return self.metrics()
        if path == "/api/health":
            return self.send_json({"status": "ok"})
        if path.startswith("/api/"):
            return self.send_json({"error": "Маршрут не найден"}, HTTPStatus.NOT_FOUND)
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/assignments":
            return self.create_assignment()
        if path == "/api/assignments/import":
            return self.import_assignments()
        if path == "/api/reminders/run":
            return self.run_reminders()
        if path.startswith("/api/sensitive/"):
            if not SENSITIVE_ENABLED:
                return self.send_json(
                    {"error": "Функция заблокирована до согласования со службой ИБ"},
                    HTTPStatus.FORBIDDEN,
                )
            return self.send_json({"error": "Интеграция ИИ не настроена"}, HTTPStatus.NOT_IMPLEMENTED)
        return self.send_json({"error": "Маршрут не найден"}, HTTPStatus.NOT_FOUND)

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/assignments/"):
            try:
                assignment_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.send_json({"error": "Некорректный идентификатор"}, HTTPStatus.BAD_REQUEST)
            return self.update_assignment(assignment_id)
        if path.startswith("/api/metrics/"):
            return self.update_metric(path.rsplit("/", 1)[1])
        return self.send_json({"error": "Маршрут не найден"}, HTTPStatus.NOT_FOUND)

    def assignments(self):
        with connect() as db:
            rows = db.execute("SELECT * FROM assignments ORDER BY due_date, id").fetchall()
        self.send_json([assignment_dict(row) for row in rows])

    def dashboard(self):
        with connect() as db:
            rows = db.execute("SELECT * FROM assignments ORDER BY due_date, id").fetchall()
            reminder_count = db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
            last_run = db.execute("SELECT MAX(sent_at) FROM reminders").fetchone()[0]
        assignments = [assignment_dict(row) for row in rows]
        active = [item for item in assignments if item["status"] != "Исполнено"]
        counts = {code: sum(item["risk"]["code"] == code for item in active) for code in ("overdue", "critical", "high", "medium", "low")}
        self.send_json(
            {
                "modules": MODULES,
                "summary": {"active": len(active), "reminders": reminder_count, "last_run": last_run, **counts},
                "assignments": assignments,
                "sensitive_enabled": SENSITIVE_ENABLED,
            }
        )

    def create_assignment(self):
        data = self.read_json()
        required = ("doc_number", "assignee", "department", "due_date")
        if not data or any(not str(data.get(key, "")).strip() for key in required):
            return self.send_json({"error": "Заполните все обязательные поля"}, HTTPStatus.BAD_REQUEST)
        try:
            date.fromisoformat(data["due_date"])
        except (TypeError, ValueError):
            return self.send_json({"error": "Некорректная дата срока"}, HTTPStatus.BAD_REQUEST)
        from mailer import valid_email
        address = str(data.get("assignee_email", "")).strip()
        if address and not valid_email(address):
            return self.send_json({"error": "Некорректный email исполнителя"}, HTTPStatus.BAD_REQUEST)
        values = [str(data[key]).strip() for key in required]
        try:
            with connect() as db:
                cursor = db.execute(
                    "INSERT INTO assignments (doc_number, assignee, department, due_date, assignee_email, is_demo) VALUES (?, ?, ?, ?, ?, 0)",
                    (*values, address),
                )
                row = db.execute("SELECT * FROM assignments WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except sqlite3.IntegrityError:
            return self.send_json({"error": "Документ с таким номером уже существует"}, HTTPStatus.CONFLICT)
        self.send_json(assignment_dict(row), HTTPStatus.CREATED)

    def import_assignments(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type or "boundary=" not in content_type:
            return self.send_json({"error": "Ожидается файл Excel в формате .xlsx"}, HTTPStatus.BAD_REQUEST)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 10 * 1024 * 1024:
            return self.send_json({"error": "Файл не должен превышать 10 МБ"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        body = self.rfile.read(length)
        boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
        parts = body.split(b"--" + boundary)
        filename = ""
        file_content = None
        for part in parts:
            if b"Content-Disposition:" not in part or b"filename=" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            header = part[:header_end].decode("utf-8", "ignore")
            filename = header.split('filename="', 1)[1].split('"', 1)[0] if 'filename="' in header else "upload.xlsx"
            file_content = part[header_end + 4:]
            if file_content.endswith(b"\r\n"):
                file_content = file_content[:-2]
            break
        if not file_content:
            return self.send_json({"error": "Файл Excel не выбран"}, HTTPStatus.BAD_REQUEST)
        try:
            rows = parse_excel_rows(file_content, filename)
        except (ValueError, zipfile.BadZipFile, ET.ParseError) as error:
            return self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        imported = 0
        skipped = 0
        with connect() as db:
            for item in rows:
                try:
                    db.execute(
                        "INSERT INTO assignments (doc_number, assignee, assignee_email, department, due_date, status, source, is_demo) VALUES (?, ?, ?, ?, ?, ?, 'Excel', 0)",
                        (item["doc_number"], item["assignee"], item["assignee_email"], item["department"], item["due_date"], item["status"]),
                    )
                    imported += 1
                except sqlite3.IntegrityError:
                    skipped += 1
        self.send_json({"imported": imported, "skipped": skipped, "total": len(rows)})

    def update_assignment(self, assignment_id):
        data = self.read_json()
        status = data.get("status") if data else None
        if status not in ("В работе", "Исполнено"):
            return self.send_json({"error": "Недопустимый статус"}, HTTPStatus.BAD_REQUEST)
        with connect() as db:
            cursor = db.execute("UPDATE assignments SET status = ? WHERE id = ?", (status, assignment_id))
            if cursor.rowcount == 0:
                return self.send_json({"error": "Поручение не найдено"}, HTTPStatus.NOT_FOUND)
            row = db.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        self.send_json(assignment_dict(row))

    def run_reminders(self):
        now = datetime.now().replace(microsecond=0)
        today_prefix = date.today().isoformat() + "%"
        created = []
        with connect() as db:
            rows = db.execute("SELECT * FROM assignments WHERE status != 'Исполнено'").fetchall()
            for row in rows:
                risk = risk_for(row["due_date"], row["status"])
                if risk["code"] == "low":
                    continue
                exists = db.execute(
                    "SELECT 1 FROM reminders WHERE assignment_id = ? AND channel = 'Протокол' AND sent_at LIKE ?",
                    (row["id"], today_prefix),
                ).fetchone()
                if exists:
                    continue
                db.execute(
                    "INSERT INTO reminders (assignment_id, level, sent_at) VALUES (?, ?, ?)",
                    (row["id"], risk["code"], now.isoformat()),
                )
                created.append({"doc_number": row["doc_number"], "assignee": row["assignee"], "risk": risk})
        self.send_json({"created": len(created), "items": created, "mode": "Протокол без внешней рассылки"})

    def metrics(self):
        with connect() as db:
            rows = db.execute("SELECT * FROM metrics ORDER BY rowid").fetchall()
        self.send_json([dict(row) for row in rows])

    def update_metric(self, metric_id):
        data = self.read_json()
        if not data:
            return self.send_json({"error": "Нет данных"}, HTTPStatus.BAD_REQUEST)
        values = []
        for key in ("baseline", "current"):
            value = data.get(key)
            if value in (None, ""):
                values.append(None)
            else:
                try:
                    values.append(float(value))
                except (TypeError, ValueError):
                    return self.send_json({"error": "Показатель должен быть числом"}, HTTPStatus.BAD_REQUEST)
        with connect() as db:
            cursor = db.execute(
                "UPDATE metrics SET baseline = ?, current = ?, measured_at = ? WHERE id = ?",
                (*values, date.today().isoformat(), metric_id),
            )
            if cursor.rowcount == 0:
                return self.send_json({"error": "Показатель не найден"}, HTTPStatus.NOT_FOUND)
            row = db.execute("SELECT * FROM metrics WHERE id = ?", (metric_id,)).fetchone()
        self.send_json(dict(row))


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    try:
        init_db()
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as error:
        show_message(
            "Контур: ошибка запуска",
            f"Не удалось запустить локальный сервис по адресу {url}.\n\n{error}",
            error=True,
        )
        raise SystemExit(1)
    if OPEN_BROWSER:
        browser_timer = threading.Timer(0.8, open_modern_browser, args=(url,))
        browser_timer.daemon = True
        browser_timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
