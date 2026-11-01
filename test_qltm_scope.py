import os
import tempfile
import unittest


TEST_DB = os.path.join(tempfile.gettempdir(), "tracuuxe-qltm-scope-test.sqlite3")
try:
    os.remove(TEST_DB)
except FileNotFoundError:
    pass

os.environ["TRACUUXE_DB_PATH"] = TEST_DB
os.environ["TRACUUXE_ADMIN_KEY"] = "ROOT-TEST-KEY"
os.environ["TRACUUXE_CUANAM_ADMIN_KEY"] = "CUANAM-TEST-KEY"

import app


class AreaScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.init_db()
        app.upsert_vehicle(
            "30A-111.11",
            "Cua Nam Test",
            "2024",
            app.VEHICLE_STATUS_ACTIVE,
            "Cửa Nam",
            "cua_nam",
        )
        app.upsert_vehicle(
            "30B-222.22",
            "Main Test",
            "2023",
            app.VEHICLE_STATUS_ACTIVE,
            "Main",
            app.MAIN_CENTER_CODE,
        )
        app.clear_cache()

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(TEST_DB)
        except (FileNotFoundError, PermissionError):
            pass

    def setUp(self):
        self.client = app.app.test_client()
        response = self.client.post("/login", data={"code": "CUANAM-TEST-KEY"})
        self.assertEqual(response.status_code, 302)

    def test_center_admin_sees_only_center_vehicles(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"30A-111.11", response.data)
        self.assertNotIn(b"30B-222.22", response.data)

    def test_center_admin_can_manage_data_but_not_root_admin(self):
        self.assertEqual(self.client.get("/admin/data").status_code, 200)
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"30A-111.11", response.data)
        self.assertNotIn(b"30B-222.22", response.data)

    def test_center_admin_can_create_code_only_for_center_vehicles(self):
        response = self.client.post(
            "/admin",
            data={
                "action": "create",
                "plates": "30A-111.11",
                "ttl_hours": "24",
            },
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db_connect()
        try:
            created = conn.execute(
                "SELECT code, plates FROM access_codes WHERE plates = ?",
                ("30A-111.11",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(created)
        self.assertEqual(created["plates"], "30A-111.11")

        before = app.db_connect()
        try:
            before_count = before.execute("SELECT COUNT(*) FROM access_codes").fetchone()[0]
        finally:
            before.close()
        response = self.client.post(
            "/admin",
            data={
                "action": "create",
                "plates": "30B-222.22",
                "ttl_hours": "24",
            },
        )
        self.assertEqual(response.status_code, 302)
        after = app.db_connect()
        try:
            after_count = after.execute("SELECT COUNT(*) FROM access_codes").fetchone()[0]
        finally:
            after.close()
        self.assertEqual(after_count, before_count)

    def test_center_admin_cannot_modify_main_vehicle(self):
        response = self.client.post(
            "/admin/data",
            data={"action": "deactivate_vehicle", "plate": "30B-222.22"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db_connect()
        try:
            row = conn.execute(
                "SELECT status FROM cars WHERE plate = ?", ("30B-222.22",)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["status"], app.VEHICLE_STATUS_ACTIVE)

    def test_center_admin_forces_parking_location_to_own_center(self):
        response = self.client.post(
            "/admin/data",
            data={
                "action": "save_vehicle",
                "form_mode": "create",
                "plate": "30A-444.44",
                "car_type": "Area Location Test",
                "manufacture_year": "2025",
                "status": app.VEHICLE_STATUS_ACTIVE,
                "parking_location": "Trụ sở",
            },
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db_connect()
        try:
            created = conn.execute(
                "SELECT center, parking_location FROM cars WHERE plate = ?",
                ("30A-444.44",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(created)
        self.assertEqual(created["center"], "cua_nam")
        self.assertEqual(created["parking_location"], "Cửa Nam")

    def test_root_admin_can_switch_between_two_centers(self):
        client = app.app.test_client()
        response = client.post("/login", data={"code": "ROOT-TEST-KEY"})
        self.assertEqual(response.status_code, 302)

        main_page = client.get("/admin/data?center=main")
        self.assertEqual(main_page.status_code, 200)
        self.assertIn(b"30B-222.22", main_page.data)
        self.assertNotIn(b"30A-111.11", main_page.data)

        area_page = client.get("/admin/data?center=cua_nam")
        self.assertEqual(area_page.status_code, 200)
        self.assertIn(b"30A-111.11", area_page.data)
        self.assertNotIn(b"30B-222.22", area_page.data)

        area_access = client.get("/admin?center=cua_nam")
        self.assertEqual(area_access.status_code, 200)

        main_dashboard = client.get("/dashboard?center=main")
        self.assertEqual(main_dashboard.status_code, 200)
        self.assertIn(b"30B-222.22", main_dashboard.data)
        self.assertNotIn(b"30A-111.11", main_dashboard.data)

        area_dashboard = client.get("/dashboard?center=cua_nam")
        self.assertEqual(area_dashboard.status_code, 200)
        self.assertIn(b"30A-111.11", area_dashboard.data)
        self.assertNotIn(b"30B-222.22", area_dashboard.data)

    def test_root_admin_can_create_vehicle_in_selected_center(self):
        client = app.app.test_client()
        response = client.post("/login", data={"code": "ROOT-TEST-KEY"})
        self.assertEqual(response.status_code, 302)

        response = client.post(
            "/admin/data",
            data={
                "action": "save_vehicle",
                "form_mode": "create",
                "plate": "30C-333.33",
                "car_type": "Root Admin Test",
                "manufacture_year": "2025",
                "status": app.VEHICLE_STATUS_ACTIVE,
                "center": "cua_nam",
                "parking_location": "Hải Phòng",
            },
        )
        self.assertEqual(response.status_code, 302)

        conn = app.db_connect()
        try:
            created = conn.execute(
                "SELECT center, parking_location FROM cars WHERE plate = ?",
                ("30C-333.33",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(created)
        self.assertEqual(created["center"], "cua_nam")
        self.assertEqual(created["parking_location"], "Hải Phòng")


if __name__ == "__main__":
    unittest.main()
