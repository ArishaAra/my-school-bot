"""
База даних: облік балансу уроків, нотатки, ДЗ, розклад
"""
import sqlite3
from datetime import datetime
from typing import Optional

class Database:
    def __init__(self, path="school.db"):
        self.path = path
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS teachers (
                    telegram_id INTEGER PRIMARY KEY,
                    name        TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS students (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id   INTEGER UNIQUE,
                    name          TEXT NOT NULL,
                    group_name    TEXT NOT NULL DEFAULT 'загальна',
                    lesson_type   TEXT NOT NULL DEFAULT 'group',
                    balance       INTEGER NOT NULL DEFAULT 0,
                    total_done    INTEGER NOT NULL DEFAULT 0,
                    active        INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS schedules (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id  INTEGER REFERENCES students(id),
                    group_name  TEXT,
                    weekday     INTEGER NOT NULL,
                    hour        INTEGER NOT NULL,
                    minute      INTEGER NOT NULL DEFAULT 0,
                    subject     TEXT NOT NULL DEFAULT 'Урок',
                    remind_min  INTEGER NOT NULL DEFAULT 60
                );

                CREATE TABLE IF NOT EXISTS lessons (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id  INTEGER NOT NULL REFERENCES students(id),
                    subject     TEXT NOT NULL,
                    note_text   TEXT,
                    homework    TEXT,
                    teacher_id  INTEGER NOT NULL,
                    done_at     TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS homework_submissions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_id    INTEGER NOT NULL REFERENCES lessons(id),
                    student_id   INTEGER NOT NULL REFERENCES students(id),
                    status       TEXT NOT NULL DEFAULT 'done',
                    submitted_at TEXT NOT NULL,
                    checked_at   TEXT
                );

                CREATE TABLE IF NOT EXISTS payments (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id  INTEGER NOT NULL REFERENCES students(id),
                    lessons_count INTEGER NOT NULL,
                    note        TEXT,
                    added_at    TEXT NOT NULL
                );
            """)

    # ── Вчитель ────────────────────────────────────────────────────────────────

    def add_teacher(self, telegram_id: int, name: str):
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO teachers(telegram_id,name) VALUES(?,?)",
                         (telegram_id, name))

    def is_teacher(self, tid: int) -> bool:
        with self._conn() as conn:
            return bool(conn.execute("SELECT 1 FROM teachers WHERE telegram_id=?", (tid,)).fetchone())

    def get_teacher_id(self) -> Optional[int]:
        with self._conn() as conn:
            r = conn.execute("SELECT telegram_id FROM teachers LIMIT 1").fetchone()
            return r[0] if r else None

    # ── Учні ───────────────────────────────────────────────────────────────────

    def add_student(self, name: str, group_name: str, lesson_type: str,
                    telegram_id: int = None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO students(telegram_id,name,group_name,lesson_type) VALUES(?,?,?,?)",
                (telegram_id, name, group_name, lesson_type)
            )
            return cur.lastrowid

    def get_student(self, sid: int) -> Optional[dict]:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
            return dict(r) if r else None

    def get_student_by_tg(self, tg_id: int) -> Optional[dict]:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM students WHERE telegram_id=?", (tg_id,)).fetchone()
            return dict(r) if r else None

    def get_all_students(self) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM students WHERE active=1 ORDER BY group_name,name").fetchall()]

    def get_group_students(self, group_name: str) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM students WHERE group_name=? AND active=1", (group_name,)).fetchall()]

    def get_groups(self) -> list:
        with self._conn() as conn:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT group_name FROM students WHERE active=1 ORDER BY group_name").fetchall()]

    def update_student_tg(self, sid: int, tg_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE students SET telegram_id=? WHERE id=?", (tg_id, sid))

    # ── Баланс уроків ──────────────────────────────────────────────────────────

    def add_payment(self, student_id: int, lessons_count: int, note: str = None):
        """Вчитель отримав оплату → додаємо уроки до балансу"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO payments(student_id,lessons_count,note,added_at) VALUES(?,?,?,?)",
                (student_id, lessons_count, note, datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
            conn.execute("UPDATE students SET balance = balance + ? WHERE id=?",
                         (lessons_count, student_id))

    def deduct_lesson(self, student_id: int):
        """Урок відбувся → мінус 1 урок з балансу"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE students SET balance = MAX(0, balance - 1), total_done = total_done + 1 WHERE id=?",
                (student_id,)
            )

    def get_balance(self, student_id: int) -> int:
        with self._conn() as conn:
            r = conn.execute("SELECT balance FROM students WHERE id=?", (student_id,)).fetchone()
            return r[0] if r else 0

    def get_low_balance_students(self, threshold: int = 2) -> list:
        """Учні з балансом <= threshold"""
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM students WHERE active=1 AND balance <= ? ORDER BY balance",
                (threshold,)).fetchall()]

    def get_payment_history(self, student_id: int) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM payments WHERE student_id=? ORDER BY added_at DESC LIMIT 10",
                (student_id,)).fetchall()]

    # ── Розклад ────────────────────────────────────────────────────────────────

    def add_schedule(self, student_id: Optional[int], group_name: Optional[str],
                     weekday: int, hour: int, minute: int, subject: str, remind_min: int) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO schedules(student_id,group_name,weekday,hour,minute,subject,remind_min) "
                "VALUES(?,?,?,?,?,?,?)",
                (student_id, group_name, weekday, hour, minute, subject, remind_min)
            )
            return cur.lastrowid

    def get_all_schedules(self) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM schedules ORDER BY weekday,hour,minute").fetchall()]

    def delete_schedule(self, lid: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM schedules WHERE id=?", (lid,))

    # ── Уроки (факт + нотатки) ─────────────────────────────────────────────────

    def add_lesson(self, student_id: int, subject: str, note_text: str,
                   homework: Optional[str], teacher_id: int) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO lessons(student_id,subject,note_text,homework,teacher_id,done_at) "
                "VALUES(?,?,?,?,?,?)",
                (student_id, subject, note_text, homework, teacher_id,
                 datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
            return cur.lastrowid

    def get_student_lessons(self, student_id: int, limit: int = 15) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM lessons WHERE student_id=? ORDER BY done_at DESC LIMIT ?",
                (student_id, limit)).fetchall()]

    def get_lesson(self, lid: int) -> Optional[dict]:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()
            return dict(r) if r else None

    # ── Домашнє завдання ───────────────────────────────────────────────────────

    def submit_homework(self, lesson_id: int, student_id: int) -> int:
        with self._conn() as conn:
            # Не дублювати
            existing = conn.execute(
                "SELECT id FROM homework_submissions WHERE lesson_id=? AND student_id=?",
                (lesson_id, student_id)).fetchone()
            if existing:
                return existing[0]
            cur = conn.execute(
                "INSERT INTO homework_submissions(lesson_id,student_id,submitted_at) VALUES(?,?,?)",
                (lesson_id, student_id, datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
            return cur.lastrowid

    def check_homework(self, sub_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE homework_submissions SET status='checked', checked_at=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), sub_id)
            )

    def get_pending_homework(self) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT hs.id, hs.submitted_at,
                       s.name  AS student_name,
                       s.telegram_id AS student_tg,
                       l.subject, l.homework
                FROM homework_submissions hs
                JOIN students s  ON s.id  = hs.student_id
                JOIN lessons  l  ON l.id  = hs.lesson_id
                WHERE hs.status = 'done'
                ORDER BY hs.submitted_at
            """).fetchall()]

    def get_submission(self, sub_id: int) -> Optional[dict]:
        with self._conn() as conn:
            r = conn.execute("""
                SELECT hs.*, s.name AS student_name, s.telegram_id AS student_tg,
                       l.subject
                FROM homework_submissions hs
                JOIN students s ON s.id = hs.student_id
                JOIN lessons  l ON l.id = hs.lesson_id
                WHERE hs.id=?
            """, (sub_id,)).fetchone()
            return dict(r) if r else None
