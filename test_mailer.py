import smtplib
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import mailer
import server


CONFIG = {"host": "smtp.example.org", "port": 587, "security": "STARTTLS",
          "sender": "office@example.org", "username": "", "password": ""}


class FakeSMTP:
    messages = []
    secured = False

    def __init__(self, host, port, timeout):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def ehlo(self):
        pass

    def starttls(self):
        self.__class__.secured = True

    def send_message(self, message):
        self.__class__.messages.append(message)


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = server.DB_PATH
        server.DB_PATH = Path(self.temp_dir.name) / "test.db"
        server.init_db()
        with server.connect() as db:
            db.execute("DELETE FROM assignments")
            self.ids = []
            for number, days, demo in (("REAL", 1, 0), ("DEMO", 1, 1), ("LATER", 9, 0)):
                cursor = db.execute(
                    "INSERT INTO assignments (doc_number, assignee, assignee_email, department, due_date, is_demo) VALUES (?, ?, ?, 'Отдел', ?, ?)",
                    (number, "Иванов И. И.", "ivanov@example.org", (date.today() + timedelta(days=days)).isoformat(), demo),
                )
                self.ids.append(cursor.lastrowid)
        FakeSMTP.messages = []
        FakeSMTP.secured = False

    def tearDown(self):
        server.DB_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_send_only_real_due_assignment_once_and_without_document_text(self):
        with patch("mailer.smtplib.SMTP", FakeSMTP):
            first = mailer.send_due_emails(self.ids, CONFIG)
            second = mailer.send_due_emails(self.ids, CONFIG)
        self.assertEqual((first["sent"], first["skipped"]), (1, 2))
        self.assertEqual((second["sent"], second["skipped"]), (0, 3))
        self.assertTrue(FakeSMTP.secured)
        self.assertEqual(len(FakeSMTP.messages), 1)
        text = FakeSMTP.messages[0].get_content()
        self.assertIn("REAL", text)
        self.assertIn("ivanov@example.org", FakeSMTP.messages[0]["To"])
        with server.connect() as db:
            channels = [row["channel"] for row in db.execute("SELECT channel FROM reminders")]
        self.assertEqual(channels, ["Email"])

    def test_failed_smtp_is_not_logged_and_can_be_retried(self):
        with patch("mailer.send_notification", side_effect=smtplib.SMTPException("unavailable")):
            failed = mailer.send_due_emails([self.ids[0]], CONFIG)
        self.assertEqual(failed["sent"], 0)
        self.assertEqual(len(failed["errors"]), 1)
        with patch("mailer.smtplib.SMTP", FakeSMTP):
            retried = mailer.send_due_emails([self.ids[0]], CONFIG)
        self.assertEqual(retried["sent"], 1)

    def test_invalid_email_is_rejected(self):
        self.assertFalse(mailer.valid_email("bad\nBcc: other@example.org"))
