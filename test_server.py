import io
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path

import server


class RiskTests(unittest.TestCase):
    def test_overdue(self):
        due = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(server.risk_for(due, "В работе")["code"], "overdue")

    def test_critical_today(self):
        self.assertEqual(server.risk_for(date.today().isoformat(), "В работе")["code"], "critical")

    def test_high_risk_three_days_before_due(self):
        due = (date.today() + timedelta(days=3)).isoformat()
        self.assertEqual(server.risk_for(due, "В работе")["code"], "high")

    def test_low_risk_more_than_week_before_due(self):
        due = (date.today() + timedelta(days=8)).isoformat()
        self.assertEqual(server.risk_for(due, "В работе")["code"], "low")

    def test_completed_overrides_due_date(self):
        due = (date.today() - timedelta(days=20)).isoformat()
        self.assertEqual(server.risk_for(due, "Исполнено")["code"], "done")

    def test_xlsx_import_reads_expected_columns(self):
        sheet = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>doc_number</t></is></c><c r="B1" t="inlineStr"><is><t>assignee</t></is></c><c r="C1" t="inlineStr"><is><t>department</t></is></c><c r="D1" t="inlineStr"><is><t>due_date</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>01-01/1</t></is></c><c r="B2" t="inlineStr"><is><t>Ivanov</t></is></c><c r="C2" t="inlineStr"><is><t>Department</t></is></c><c r="D2" t="inlineStr"><is><t>2026-10-01</t></is></c></row></sheetData></worksheet>'''
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        rows = server.parse_excel_rows(output.getvalue(), "tasks.xlsx")
        self.assertEqual(rows[0]["doc_number"], "01-01/1")
        self.assertEqual(rows[0]["due_date"], "2026-10-01")

    def test_xlsx_import_finds_header_and_converts_excel_date(self):
        sheet = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Report</t></is></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>document number</t></is></c><c r="B3" t="inlineStr"><is><t>assignee</t></is></c><c r="C3" t="inlineStr"><is><t>department</t></is></c><c r="D3" t="inlineStr"><is><t>due date</t></is></c></row><row r="4"><c r="A4" t="inlineStr"><is><t>01-01/2</t></is></c><c r="B4" t="inlineStr"><is><t>Petrov</t></is></c><c r="C4" t="inlineStr"><is><t>Office</t></is></c><c r="D4"><v>46306</v></c></row></sheetData></worksheet>'''
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("xl/worksheets/sheet2.xml", sheet)
        rows = server.parse_excel_rows(output.getvalue(), "tasks.xlsm")
        self.assertEqual(rows[0]["due_date"], "2026-10-11")

    def test_common_date_formats(self):
        self.assertEqual(server.normalize_excel_date("01.10.2026"), "2026-10-01")
        self.assertEqual(server.normalize_excel_date("2026-10-01 00:00:00"), "2026-10-01")

    def test_common_russian_header_variants(self):
        self.assertEqual(server.infer_header_field(server.normalize_header("Рег. №")), "doc_number")
        self.assertEqual(server.infer_header_field(server.normalize_header("Ответственное структурное подразделение")), "department")
        self.assertIsNone(server.infer_header_field(server.normalize_header("№ п/п")))

    def test_xlsx_import_allows_missing_department(self):
        sheet = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>document number</t></is></c><c r="B1" t="inlineStr"><is><t>assignee</t></is></c><c r="C1" t="inlineStr"><is><t>due date</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>01-01/3</t></is></c><c r="B2" t="inlineStr"><is><t>Sidorov</t></is></c><c r="C2" t="inlineStr"><is><t>01.10.2026</t></is></c></row></sheetData></worksheet>'''
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        rows = server.parse_excel_rows(output.getvalue(), "tasks.xlsx")
        self.assertEqual(rows[0]["department"], "Не указано")

    def test_xlsx_import_reads_optional_email(self):
        sheet = '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>document number</t></is></c><c r="B1" t="inlineStr"><is><t>assignee</t></is></c><c r="C1" t="inlineStr"><is><t>due date</t></is></c><c r="D1" t="inlineStr"><is><t>Электронная почта</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>45</t></is></c><c r="B2" t="inlineStr"><is><t>Ivanov</t></is></c><c r="C2" t="inlineStr"><is><t>01.10.2026</t></is></c><c r="D2" t="inlineStr"><is><t>ivanov@example.org</t></is></c></row></sheetData></worksheet>'''
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", sheet.encode("utf-8"))
        self.assertEqual(server.parse_excel_rows(output.getvalue(), "tasks.xlsx")[0]["assignee_email"], "ivanov@example.org")


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = server.DB_PATH
        server.DB_PATH = Path(self.temp_dir.name) / "test.db"
        server.init_db()

    def tearDown(self):
        server.DB_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_seed_has_no_invented_metrics(self):
        with server.connect() as db:
            rows = db.execute("SELECT baseline, current FROM metrics").fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(row["baseline"] is None and row["current"] is None for row in rows))

    def test_seed_assignments_are_marked_demo(self):
        with server.connect() as db:
            rows = db.execute("SELECT is_demo FROM assignments").fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(row["is_demo"] == 1 for row in rows))

    def test_existing_assignments_gain_email_without_loss(self):
        with server.connect() as db:
            db.execute("CREATE TABLE old_assignments AS SELECT id, doc_number, assignee, department, due_date, status, source, is_demo, created_at FROM assignments")
            db.execute("DROP TABLE assignments")
            db.execute("ALTER TABLE old_assignments RENAME TO assignments")
        server.init_db()
        with server.connect() as db:
            rows = db.execute("SELECT doc_number, assignee_email FROM assignments").fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["assignee_email"] == "" for row in rows))

    def test_deleted_last_assignment_is_not_reseeded(self):
        with server.connect() as db:
            db.execute("DELETE FROM reminders")
            db.execute("DELETE FROM assignments")
        server.init_db()
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
