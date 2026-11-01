import os
import tempfile
import unittest


class VehicleRecordRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["TRACUUXE_DB_PATH"] = os.path.join(cls.temp_dir.name, "records.sqlite3")
        os.environ["TRACUUXE_ADMIN_KEY"] = "test-admin"
        import app as vehicle_app

        cls.app = vehicle_app
        vehicle_app.DATA_DB_PATH = os.environ["TRACUUXE_DB_PATH"]
        vehicle_app.ADMIN_KEY = "test-admin"
        vehicle_app.AREA_ADMIN_KEYS = {area_code: "" for area_code in vehicle_app.AREA_ADMIN_SPECS}
        vehicle_app.init_db()
        vehicle_app.upsert_vehicle("30A-123.45", "Camry", "2020", vehicle_app.VEHICLE_STATUS_ACTIVE)

    @classmethod
    def tearDownClass(cls):
        cls.app.clear_cache()
        try:
            cls.temp_dir.cleanup()
        except (PermissionError, OSError):
            pass

    def setUp(self):
        self.app.clear_cache()
        conn = self.app.db_connect()
        try:
            conn.execute("DELETE FROM service_history")
            conn.execute("DELETE FROM oil_records")
            conn.execute("DELETE FROM registration_records")
            conn.commit()
        finally:
            conn.close()

    def test_periodic_schedule_uses_latest_actual_date_and_one_year(self):
        self.assertTrue(self.app.add_history_record("30A-123.45", "2026-10-17", "Bảo dưỡng", "1000000", self.app.COST_TYPE_PERIODIC))
        schedule = self.app.maintenance_schedule(self.app.load_data(["history"])["history"], "30A-123.45")
        self.assertEqual(schedule["next_month"], "10/2027")
        self.assertEqual(schedule["next_tooltip"], "17/10/2027")
        self.assertTrue(self.app.add_history_record("30A-123.45", "2027-03-02", "Bảo dưỡng tiếp", "1200000", self.app.COST_TYPE_PERIODIC))
        schedule = self.app.maintenance_schedule(self.app.load_data(["history"])["history"], "30A-123.45")
        self.assertEqual(schedule["next_month"], "03/2028")

    def test_oil_first_record_requires_previous_odometer(self):
        ok, _ = self.app.save_oil_record("30A-123.45", "2026-10-18", "100000", "", "800000")
        self.assertFalse(ok)
        ok, _ = self.app.save_oil_record("30A-123.45", "2026-10-18", "100000", "95000", "800000")
        self.assertTrue(ok)
        ok, _ = self.app.save_oil_record("30A-123.45", "2027-01-18", "105000", "", "850000")
        self.assertTrue(ok)
        rows = self.app.build_oil_rows(self.app.load_data(["oil"])["oil"], "30A-123.45")
        self.assertEqual(sorted(row["distance"] for row in rows), ["5.000", "5.000"])

    def test_registration_keeps_date_and_month_separately(self):
        ok, _ = self.app.save_registration_record("30A-123.45", "2026-10-18", "2027-10", "10483000")
        self.assertTrue(ok)
        rows = self.app.build_registration_rows(self.app.load_data(["registration"])["registration"], "30A-123.45")
        self.assertEqual(rows[0]["date"], "18/10/2026")
        self.assertEqual(rows[0]["due_month"], "10/2027")


if __name__ == "__main__":
    unittest.main()
