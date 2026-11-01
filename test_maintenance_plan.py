import os
import tempfile
import unittest
from datetime import timedelta


TEST_DB = os.path.join(tempfile.gettempdir(), "tracuuxe-maintenance-plan-test.sqlite3")
try:
    os.remove(TEST_DB)
except FileNotFoundError:
    pass

os.environ["TRACUUXE_DB_PATH"] = TEST_DB
os.environ["TRACUUXE_ADMIN_KEY"] = "ROOT-PLAN-TEST"
os.environ["TRACUUXE_CUANAM_ADMIN_KEY"] = "CUANAM-PLAN-TEST"

import app


def login(client, code):
    client.get("/login")
    with client.session_transaction() as flask_session:
        token = flask_session[app.CSRF_SESSION_KEY]
    return client.post("/login", data={"code": code, "_csrf_token": token})


class MaintenancePlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.DATA_DB_PATH = TEST_DB
        app.ADMIN_KEY = "ROOT-PLAN-TEST"
        app.AREA_ADMIN_KEYS = {area_code: "" for area_code in app.AREA_ADMIN_SPECS}
        app.AREA_ADMIN_KEYS["cua_nam"] = "CUANAM-PLAN-TEST"
        app.init_db()
        app.upsert_vehicle("30C-333.33", "Plan Test", "2024", app.VEHICLE_STATUS_ACTIVE, "", app.MAIN_CENTER_CODE)

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(TEST_DB)
        except (FileNotFoundError, PermissionError):
            pass

    def setUp(self):
        with app.db_connect() as conn:
            conn.execute("DELETE FROM next_service_logs WHERE plate = '30C-333.33'")
            conn.execute("DELETE FROM next_services WHERE plate = '30C-333.33'")
            conn.execute("DELETE FROM service_history WHERE plate = '30C-333.33'")
        app.clear_cache()

    def test_future_plan_has_no_cost_and_can_be_edited(self):
        tomorrow = (app.now_vn().date() + timedelta(days=1)).isoformat()
        day_after = (app.now_vn().date() + timedelta(days=2)).isoformat()
        ok, message = app.save_maintenance_plan("30C-333.33", tomorrow, "Thay dầu", app.PLAN_TYPE_PERIODIC)
        self.assertTrue(ok, message)
        ok, message = app.save_maintenance_plan("30C-333.33", day_after, "Kiểm tra phanh", app.PLAN_TYPE_REPAIR)
        self.assertTrue(ok, message)
        with app.db_connect() as conn:
            row = conn.execute("SELECT next_due, suggestion, plan_type, oil_cost FROM next_services WHERE plate = '30C-333.33'").fetchone()
            history_count = conn.execute("SELECT COUNT(*) FROM service_history WHERE plate = '30C-333.33'").fetchone()[0]
        self.assertEqual(row["next_due"], day_after)
        self.assertEqual(row["suggestion"], "Kiểm tra phanh")
        self.assertEqual(row["plan_type"], app.PLAN_TYPE_REPAIR)
        self.assertEqual(row["oil_cost"], 0)
        self.assertEqual(history_count, 0)

    def test_plan_must_be_after_today(self):
        today = app.now_vn().date().isoformat()
        ok, message = app.save_maintenance_plan("30C-333.33", today, "Không hợp lệ")
        self.assertFalse(ok)
        self.assertIn("lớn hơn", message)

    def test_completion_creates_history_and_clears_plan(self):
        tomorrow = (app.now_vn().date() + timedelta(days=1)).isoformat()
        app.save_maintenance_plan("30C-333.33", tomorrow, "Bảo dưỡng định kỳ", app.PLAN_TYPE_PERIODIC)
        with app.db_connect() as conn:
            conn.execute("UPDATE next_services SET next_due = ? WHERE plate = '30C-333.33'", (app.now_vn().date().isoformat(),))
        ok, message = app.complete_maintenance_plan("30C-333.33", app.now_vn().date().isoformat(), "Đã thay dầu", "800.000", app.PLAN_TYPE_PERIODIC)
        self.assertTrue(ok, message)
        with app.db_connect() as conn:
            plan = conn.execute("SELECT next_due, suggestion, last_service FROM next_services WHERE plate = '30C-333.33'").fetchone()
            history = conn.execute("SELECT service_date, content, cost, cost_type FROM service_history WHERE plate = '30C-333.33'").fetchone()
        self.assertIsNone(plan["next_due"])
        self.assertIsNone(plan["suggestion"])
        self.assertEqual(plan["last_service"], app.now_vn().date().isoformat())
        self.assertEqual(history["content"], "Đã thay dầu")
        self.assertEqual(history["cost"], 800000)
        self.assertEqual(history["cost_type"], app.PLAN_TYPE_PERIODIC)

    def test_completion_before_due_is_rejected_and_standalone_history_still_works(self):
        tomorrow = (app.now_vn().date() + timedelta(days=1)).isoformat()
        app.save_maintenance_plan("30C-333.33", tomorrow, "Kế hoạch tương lai")
        ok, message = app.complete_maintenance_plan("30C-333.33", app.now_vn().date().isoformat(), "Thực hiện sớm", "100.000")
        self.assertFalse(ok)
        self.assertIn("Chưa đến", message)
        self.assertTrue(app.add_history_record("30C-333.33", app.now_vn().date().isoformat(), "Sửa chữa độc lập", "100.000"))

    def test_due_plan_alert_is_visible_to_root_and_vehicle_code(self):
        today = app.now_vn().date().isoformat()
        with app.db_connect() as conn:
            conn.execute(
                "INSERT INTO next_services (plate, next_due, suggestion, plan_type) VALUES (?, ?, ?, ?)",
                ("30C-333.33", today, "Kiểm tra lốp", app.PLAN_TYPE_REPAIR),
            )
        client = app.app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["access_info"] = {"code": "ROOT-PLAN-TEST", "bien_so": "ALL"}
        with app.app.test_request_context():
            from flask import session
            session["access_info"] = {"code": "ROOT-PLAN-TEST", "bien_so": "ALL"}
            self.assertEqual(len(app.pending_maintenance_plan_alerts()), 1)
            session["access_info"] = {"code": "VEHICLE-CODE", "bien_so": "30C-333.33"}
            self.assertEqual(len(app.pending_maintenance_plan_alerts()), 1)

    def test_detail_and_admin_maintenance_pages_render(self):
        client = app.app.test_client()
        response = login(client, "ROOT-PLAN-TEST")
        self.assertEqual(response.status_code, 302)
        list_response = client.get("/detail")
        list_body = list_response.get_data(as_text=True)
        self.assertEqual(list_response.status_code, 200)
        self.assertIn("Danh sách xe", list_body)
        self.assertIn("Xem chi tiết", list_body)
        tomorrow = (app.now_vn().date() + timedelta(days=1)).isoformat()
        app.save_maintenance_plan("30C-333.33", tomorrow, "Kiểm tra phanh", app.PLAN_TYPE_REPAIR)
        self.assertEqual(client.get("/detail?plate=30C-333.33").status_code, 200)
        self.assertEqual(client.get("/admin/data?tab=maintenance&plate=30C-333.33").status_code, 200)
        with app.db_connect() as conn:
            conn.execute("UPDATE next_services SET next_due = ? WHERE plate = '30C-333.33'", (app.now_vn().date().isoformat(),))
        self.assertEqual(client.get("/detail?plate=30C-333.33").status_code, 200)
        self.assertEqual(client.get("/admin/data?tab=maintenance&plate=30C-333.33").status_code, 200)

    def test_maintenance_alerts_page_lists_update_popup(self):
        client = app.app.test_client()
        response = login(client, "ROOT-PLAN-TEST")
        self.assertEqual(response.status_code, 302)
        today = app.now_vn().date().isoformat()
        with app.db_connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO next_services (plate, next_due, suggestion, plan_type) VALUES (?, ?, ?, ?)",
                ("30C-333.33", today, "Kiểm tra phanh", app.PLAN_TYPE_REPAIR),
            )
        app.clear_cache()
        response = client.get("/maintenance-alerts")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Danh sách kế hoạch dự kiến chưa cập nhật", body)
        self.assertIn("data-alert-complete-open", body)
        self.assertIn("alert-complete-modal", body)


if __name__ == "__main__":
    unittest.main()
