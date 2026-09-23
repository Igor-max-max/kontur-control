import re
import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import parseaddr


EMAIL_PATTERN = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


def valid_email(address):
    if not address or "\n" in address or "\r" in address:
        return False
    return parseaddr(address)[1] == address and bool(EMAIL_PATTERN.fullmatch(address))


def send_notification(assignment, config):
    recipient = assignment["assignee_email"]
    sender = config["sender"]
    if not valid_email(recipient) or not valid_email(sender):
        raise ValueError("Некорректный адрес отправителя или получателя")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"Напоминание о сроке: {assignment['doc_number']}"
    message.set_content(
        f"Исполнитель: {assignment['assignee']}\n"
        f"Документ: {assignment['doc_number']}\n"
        f"Срок исполнения: {assignment['due_date']}\n"
        f"Статус: {assignment['status']}\n\n"
        "Проверьте исполнение поручения в СЭД.\n"
    )
    client = smtplib.SMTP_SSL if config["security"] == "SSL" else smtplib.SMTP
    with client(config["host"], int(config["port"]), timeout=10) as smtp:
        if config["security"] == "STARTTLS":
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        if config["username"]:
            smtp.login(config["username"], config["password"])
        smtp.send_message(message)


def send_due_emails(assignment_ids, config):
    import server

    if not config.get("host") or not valid_email(config.get("sender", "")):
        raise ValueError("Укажите SMTP-сервер и адрес отправителя")
    if config.get("security") not in ("SSL", "STARTTLS"):
        raise ValueError("Выберите защищённое соединение SSL или STARTTLS")
    if bool(config.get("username")) != bool(config.get("password")):
        raise ValueError("Для входа в SMTP укажите логин и пароль")
    try:
        port = int(config.get("port"))
    except (TypeError, ValueError) as error:
        raise ValueError("Некорректный порт SMTP") from error
    if not 1 <= port <= 65535:
        raise ValueError("Некорректный порт SMTP")

    result = {"sent": 0, "skipped": 0, "errors": []}
    today = date.today().isoformat() + "%"
    for assignment_id in dict.fromkeys(assignment_ids):
        with server.connect() as db:
            assignment = db.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
            already_sent = db.execute(
                "SELECT 1 FROM reminders WHERE assignment_id = ? AND channel = 'Email' AND sent_at LIKE ?",
                (assignment_id, today),
            ).fetchone()
        if (assignment is None or assignment["is_demo"] or assignment["status"] == "Исполнено"
                or server.risk_for(assignment["due_date"], assignment["status"])["code"] == "low"
                or not assignment["assignee_email"] or already_sent):
            result["skipped"] += 1
            continue
        try:
            send_notification(assignment, config)
        except (OSError, smtplib.SMTPException, ValueError) as error:
            result["errors"].append(f"{assignment['doc_number']}: {error}")
            continue
        with server.connect() as db:
            db.execute(
                "INSERT INTO reminders (assignment_id, level, sent_at, channel) VALUES (?, ?, ?, 'Email')",
                (assignment_id, server.risk_for(assignment["due_date"], assignment["status"])["code"],
                 datetime.now().replace(microsecond=0).isoformat()),
            )
        result["sent"] += 1
    return result
