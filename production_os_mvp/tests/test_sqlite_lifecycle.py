import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


class SQLiteLifecycleTests(unittest.TestCase):
    def test_close_and_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "lifecycle.sqlite3"
            with patch.object(engine, "DB", target):
                with engine.connect() as db:
                    db.execute("create table example (value integer)")
                    db.execute("insert into example values (1)")
                with self.assertRaises(sqlite3.ProgrammingError):
                    db.execute("select 1")
                with engine.connect() as verify:
                    self.assertEqual(verify.execute("select value from example").fetchone()[0], 1)
                target.unlink()

    def test_rollback_and_close_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "lifecycle.sqlite3"
            with patch.object(engine, "DB", target):
                with engine.connect() as db:
                    db.execute("create table example (value integer)")
                with self.assertRaises(RuntimeError):
                    with engine.connect() as db:
                        db.execute("insert into example values (2)")
                        raise RuntimeError("test rollback")
                with self.assertRaises(sqlite3.ProgrammingError):
                    db.execute("select 1")
                with engine.connect() as verify:
                    self.assertEqual(verify.execute("select count(*) from example").fetchone()[0], 0)
                target.unlink()


if __name__ == "__main__":
    unittest.main()
