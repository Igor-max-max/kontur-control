import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import server
from desktop import KonturApp
from tkinter import Toplevel, ttk


class ProactiveControlTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = server.DB_PATH
        server.DB_PATH = Path(self.temp_dir.name) / "test.db"
        server.init_db()
        with server.connect() as db:
            db.execute("DELETE FROM assignments")
            for number, days in (("OVERDUE", -1), ("CRITICAL", 1), ("HIGH", 3), ("LOW", 9)):
                db.execute(
                    "INSERT INTO assignments (doc_number, assignee, department, due_date) VALUES (?, 'Исполнитель', 'Отдел', ?)",
                    (number, (date.today() + timedelta(days=days)).isoformat()),
                )
        self.app = KonturApp()

    def tearDown(self):
        self.app.destroy()
        server.DB_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_only_control_tabs_and_risk_colors(self):
        titles = [self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()]
        self.assertEqual(titles, ["Контроль поручений", "Показатели пилота"])
        self.assertEqual(self.app.tree["columns"], ("number", "assignee", "due", "risk", "status"))
        self.assertEqual(int(ttk.Style(self.app).lookup("Treeview", "rowheight")), 48)
        row_tags = {self.app.tree.item(item, "values")[0]: self.app.tree.item(item, "tags") for item in self.app.tree.get_children()}
        self.assertEqual(row_tags["OVERDUE"], ("overdue",))
        self.assertEqual(row_tags["CRITICAL"], ("critical",))
        self.assertEqual(row_tags["HIGH"], ("high",))

    def test_date_display_and_centered_columns(self):
        for column in self.app.tree["columns"]:
            self.assertEqual(str(self.app.tree.column(column, "anchor")), "center")
            self.assertEqual(str(self.app.tree.heading(column, "anchor")), "center")
        rows = {self.app.tree.item(item, "values")[0]: self.app.tree.item(item, "values") for item in self.app.tree.get_children()}
        self.assertEqual(rows["HIGH"][2], (date.today() + timedelta(days=3)).strftime("%d.%m.%Y"))

    def test_grid_separates_rows_and_columns_after_filtering(self):
        self.app.update()
        tree = self.app.tree
        first = tree.get_children()[0]
        row_bottom = sum(tree.bbox(first)[1::2]) - 1
        column = tree.bbox(first, "#1")
        column_right = column[0] + column[2] - 1
        horizontal = [line for line in self.app._grid_lines if line.winfo_manager() == "place" and line.winfo_height() == 1]
        vertical = [line for line in self.app._grid_lines if line.winfo_manager() == "place" and line.winfo_width() == 1]
        self.assertTrue(any(line.winfo_y() == row_bottom for line in horizontal))
        self.assertTrue(any(line.winfo_x() == column_right for line in vertical))
        self.app.risk_filter.set("Высокий")
        self.app.apply_filters()
        self.app.update()
        self.assertEqual(len(self.app.tree.get_children()), 1)
        horizontal = [line for line in self.app._grid_lines if line.winfo_manager() == "place" and line.winfo_height() == 1]
        self.assertEqual(len(horizontal), 1)

    def test_grid_follows_scrolling(self):
        with server.connect() as db:
            db.executemany(
                "INSERT INTO assignments (doc_number, assignee, department, due_date) VALUES (?, 'Исполнитель', 'Отдел', ?)",
                [(f"EXTRA-{i}", (date.today() + timedelta(days=i)).isoformat()) for i in range(35)],
            )
        self.app.refresh()
        self.app.update()
        self.app.tree.yview_moveto(0.75)
        self.app.update()
        tree = self.app.tree
        visible = tree.identify_row(tree.winfo_height() // 2)
        self.assertTrue(visible)
        bottom = tree.bbox(visible)[1] + tree.bbox(visible)[3] - 1
        self.assertTrue(any(line.winfo_manager() == "place" and line.winfo_height() == 1
                            and line.winfo_y() == bottom for line in self.app._grid_lines))

    def test_ministry_name_hidden_beside_assignee_without_changing_record(self):
        full_name = "Иванов И.И. МИНИСТЕРСТВО ОБРАЗОВАНИЯ И НАУКИ ЛУГАНСКОЙ НАРОДНОЙ РЕСПУБЛИКИ"
        with server.connect() as db:
            db.execute("UPDATE assignments SET assignee = ? WHERE doc_number = 'HIGH'", (full_name,))
            db.execute("UPDATE assignments SET assignee = ? WHERE doc_number = 'LOW'", ("Минобрнауки ЛНР / Петров П. П.",))
        self.app.refresh()
        rows = {self.app.tree.item(item, "values")[0]: self.app.tree.item(item, "values")[1] for item in self.app.tree.get_children()}
        self.assertEqual(rows["HIGH"], "Иванов И.И.")
        self.assertEqual(rows["LOW"], "Петров П. П.")
        self.assertIn("Иванов И.И.", self.app.assignee_filter["values"])
        self.app.assignee_filter.set("Иванов")
        self.app.apply_filters()
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.app.tree.selection_set(self.app.tree.get_children()[0])
        self.app.edit_selected()
        dialog = next(child for child in self.app.winfo_children() if isinstance(child, Toplevel))
        self.assertEqual(dialog.grid_slaves(row=1, column=1)[0].get(), "Иванов И.И.")
        dialog.destroy()
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT assignee FROM assignments WHERE doc_number = 'HIGH'").fetchone()[0], full_name)

    def test_reminders_are_formed_once_per_day_for_approaching_deadlines(self):
        with patch("desktop.messagebox.showinfo"):
            self.app.run_reminders()
            self.app.run_reminders()
        with server.connect() as db:
            rows = db.execute(
                "SELECT a.doc_number FROM reminders r JOIN assignments a ON a.id = r.assignment_id ORDER BY a.doc_number"
            ).fetchall()
        self.assertEqual([row["doc_number"] for row in rows], ["CRITICAL", "HIGH", "OVERDUE"])

    def test_filters_by_assignee_and_due_date_without_changing_data(self):
        with server.connect() as db:
            db.execute("UPDATE assignments SET assignee = 'Другой исполнитель' WHERE doc_number = 'HIGH'")
        self.app.refresh()
        self.app.assignee_filter.set("Другой")
        self.app.apply_filters()
        self.assertEqual([self.app.tree.item(item, "values")[0] for item in self.app.tree.get_children()], ["HIGH"])
        self.app.date_to.insert(0, (date.today() + timedelta(days=1)).isoformat())
        self.app.apply_filters()
        self.assertEqual(len(self.app.tree.get_children()), 0)
        self.app.reset_filters()
        self.assertEqual(len(self.app.tree.get_children()), 4)

    def test_risk_filter_combines_with_other_filters_and_resets(self):
        with server.connect() as db:
            db.execute("INSERT INTO assignments (doc_number, assignee, department, due_date) VALUES (?, ?, ?, ?)",
                       ("MEDIUM", "Другой", "Отдел", (date.today() + timedelta(days=5)).isoformat()))
        self.app.refresh()
        self.assertEqual(self.app.risk_filter.get(), "Все уровни")
        self.assertEqual(str(self.app.risk_filter.cget("state")), "readonly")
        for level, expected in (("Срок нарушен", "OVERDUE"), ("Критический", "CRITICAL"),
                                ("Высокий", "HIGH"), ("Средний", "MEDIUM"), ("Низкий", "LOW")):
            self.app.risk_filter.set(level)
            self.app.apply_filters()
            self.assertEqual([self.app.tree.item(item, "values")[0] for item in self.app.tree.get_children()], [expected])
        self.app.risk_filter.set("Высокий")
        self.app.assignee_filter.set("Исполнитель")
        self.app.date_from.insert(0, (date.today() + timedelta(days=2)).isoformat())
        self.app.date_to.insert(0, (date.today() + timedelta(days=4)).isoformat())
        self.app.apply_filters()
        self.assertEqual([self.app.tree.item(item, "values")[0] for item in self.app.tree.get_children()], ["HIGH"])
        self.app.assignee_filter.set("несуществующий")
        self.app.apply_filters()
        self.assertEqual(len(self.app.tree.get_children()), 0)
        self.app.reset_filters()
        self.assertEqual(self.app.risk_filter.get(), "Все уровни")
        self.assertEqual(len(self.app.tree.get_children()), 5)

    def test_risk_filter_recalculates_after_status_change(self):
        self.app.risk_filter.set("Критический")
        self.app.apply_filters()
        selected = self.app.tree.get_children()[0]
        self.app.tree.selection_set(selected)
        self.app.complete_selected()
        self.assertEqual(len(self.app.tree.get_children()), 0)
        self.app.risk_filter.set("Исполнено")
        self.app.apply_filters()
        self.assertEqual([self.app.tree.item(item, "values")[0] for item in self.app.tree.get_children()], ["CRITICAL"])

    def test_edit_selected_updates_record_and_recalculates_risk(self):
        item = next(item for item in self.app.tree.get_children() if self.app.tree.item(item, "values")[0] == "HIGH")
        self.app.tree.selection_set(item)
        self.app.edit_selected()
        dialog = next(child for child in self.app.winfo_children() if isinstance(child, Toplevel))

        def replace(row, value):
            entry = dialog.grid_slaves(row=row, column=1)[0]
            entry.delete(0, "end")
            entry.insert(0, value)

        replace(0, "ИЗМЕНЕН")
        replace(1, "Новый исполнитель")
        replace(2, "new@example.org")
        replace(3, "Новый отдел")
        self.assertEqual(dialog.grid_slaves(row=4, column=1)[0].get(), (date.today() + timedelta(days=3)).strftime("%d.%m.%Y"))
        replace(4, (date.today() + timedelta(days=1)).strftime("%d.%m.%Y"))
        dialog.grid_slaves(row=6)[0].invoke()
        with server.connect() as db:
            row = db.execute("SELECT * FROM assignments WHERE id = ?", (int(item),)).fetchone()
        self.assertEqual((row["doc_number"], row["assignee"], row["assignee_email"], row["department"]),
                         ("ИЗМЕНЕН", "Новый исполнитель", "new@example.org", "Новый отдел"))
        self.assertEqual(self.app.tree.item(item, "tags"), ("critical",))

    def test_delete_selected_requires_confirmation_and_removes_history(self):
        item = self.app.tree.get_children()[0]
        self.app.tree.selection_set(item)
        with server.connect() as db:
            db.execute("INSERT INTO reminders (assignment_id, level, sent_at) VALUES (?, 'overdue', '2026-01-01')", (int(item),))
        with patch("desktop.messagebox.askyesno", return_value=False):
            self.app.delete_selected()
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 4)
        with patch("desktop.messagebox.askyesno", return_value=True) as confirm:
            self.app.delete_selected()
        self.assertIn("OVERDUE", confirm.call_args.args[1])
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments WHERE id = ?", (int(item),)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reminders WHERE assignment_id = ?", (int(item),)).fetchone()[0], 0)

    def test_clear_assignments_requires_confirmation_and_clears_hidden_rows_and_history(self):
        self.app.risk_filter.set("Высокий")
        self.app.apply_filters()
        self.assertEqual(len(self.app.tree.get_children()), 1)
        with server.connect() as db:
            db.execute("INSERT INTO reminders (assignment_id, level, sent_at) SELECT id, 'high', '2026-01-01' FROM assignments LIMIT 1")
            db.execute("UPDATE metrics SET baseline = 5 WHERE id = 'registration_time'")
        with patch("desktop.messagebox.askyesno", return_value=False):
            self.app.clear_assignments()
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 1)
        with patch("desktop.messagebox.askyesno", return_value=True) as confirm:
            self.app.clear_assignments()
        self.assertIn("(4)", confirm.call_args.args[1])
        self.assertEqual(len(self.app.tree.get_children()), 0)
        self.assertEqual(self.app.risk_filter.get(), "Все уровни")
        server.init_db()
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT baseline FROM metrics WHERE id = 'registration_time'").fetchone()[0], 5)
