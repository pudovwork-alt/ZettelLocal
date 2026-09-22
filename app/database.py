from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _column_names(con: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}

    def _init_schema(self) -> None:
        with self._lock, self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id, sort_order, title);

                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_notes_title ON notes(title);
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    target_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    relation_type TEXT NOT NULL DEFAULT 'связано с',
                    created_at TEXT NOT NULL,
                    UNIQUE(source_id, target_id, relation_type)
                );
                CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
                CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    query TEXT NOT NULL DEFAULT '',
                    source_note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
                    target_note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
                    rating INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_query ON feedback(query);

                CREATE TABLE IF NOT EXISTS verified_answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    source_note_ids TEXT NOT NULL DEFAULT '[]',
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

            # Migration from ZettelLocal 1.x.
            note_columns = self._column_names(con, "notes")
            if "category_id" not in note_columns:
                con.execute("ALTER TABLE notes ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
            con.execute("CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category_id)")

            # Migration safety: an existing 1.x database can contain notes while the
            # newly added categories table is still empty. Always create a visible
            # root folder in that case, without moving or deleting existing notes.
            notes_count = int(con.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
            categories_count = int(con.execute("SELECT COUNT(*) FROM categories").fetchone()[0])
            root_category_id: int | None = None
            if categories_count == 0:
                now = self.now()
                cur = con.execute(
                    "INSERT INTO categories(title, parent_id, sort_order, created_at, updated_at) VALUES(?,?,?,?,?)",
                    ("База знаний", None, 0, now, now),
                )
                root_category_id = int(cur.lastrowid)

            if notes_count == 0:
                now = self.now()
                if root_category_id is None:
                    row = con.execute(
                        "SELECT id FROM categories WHERE parent_id IS NULL ORDER BY sort_order, id LIMIT 1"
                    ).fetchone()
                    root_category_id = int(row[0]) if row else None
                con.execute(
                    "INSERT INTO notes(title, content, tags, favorite, category_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        "Добро пожаловать в ZettelLocal",
                        "# Ваша локальная база знаний\n\n"
                        "Создавайте короткие атомарные заметки и связывайте их: [[Пример заметки]].\n\n"
                        "## Навигация\n"
                        "- папки, подпапки и заметки находятся в каталоге слева;\n"
                        "- [[вики-ссылки]] становятся обычными кликабельными ссылками в режиме «Вместе» и «Чтение»;\n"
                        "- клик по несуществующей [[Новой заметке]] создаёт её;\n"
                        "- заметку можно перенести в папку через поле «Категория» или перетаскиванием.\n\n"
                        "> Все данные сохраняются только на этом компьютере.",
                        json.dumps(["старт", "справка"], ensure_ascii=False),
                        1,
                        root_category_id,
                        now,
                        now,
                    ),
                )

    @staticmethod
    def row_to_note(row: sqlite3.Row) -> dict[str, Any]:
        note = dict(row)
        try:
            note["tags"] = json.loads(note.get("tags") or "[]")
        except json.JSONDecodeError:
            note["tags"] = []
        note["favorite"] = bool(note.get("favorite"))
        return note

    @staticmethod
    def row_to_answer(row: sqlite3.Row) -> dict[str, Any]:
        answer = dict(row)
        for key in ("source_note_ids", "tags"):
            try:
                answer[key] = json.loads(answer.get(key) or "[]")
            except json.JSONDecodeError:
                answer[key] = []
        return answer

    def descendant_category_ids(self, category_id: int) -> list[int]:
        with self._lock, self.connect() as con:
            rows = con.execute(
                """
                WITH RECURSIVE descendants(id) AS (
                    SELECT id FROM categories WHERE id = ?
                    UNION ALL
                    SELECT c.id FROM categories c JOIN descendants d ON c.parent_id = d.id
                )
                SELECT id FROM descendants
                """,
                (category_id,),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def list_notes(
        self,
        query: str = "",
        favorite: bool | None = None,
        category_id: int | None = None,
        include_children: bool = True,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT n.*, c.title AS category_title FROM notes n "
            "LEFT JOIN categories c ON c.id = n.category_id"
        )
        params: list[Any] = []
        clauses: list[str] = []
        if query:
            clauses.append("(n.title LIKE ? OR n.content LIKE ? OR n.tags LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if favorite is not None:
            clauses.append("n.favorite = ?")
            params.append(1 if favorite else 0)
        if category_id is not None:
            if category_id == 0:
                clauses.append("n.category_id IS NULL")
            elif include_children:
                ids = self.descendant_category_ids(category_id)
                if not ids:
                    return []
                placeholders = ",".join("?" for _ in ids)
                clauses.append(f"n.category_id IN ({placeholders})")
                params.extend(ids)
            else:
                clauses.append("n.category_id = ?")
                params.append(category_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY n.updated_at DESC"
        with self._lock, self.connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self.row_to_note(row) for row in rows]

    def get_note(self, note_id: int) -> dict[str, Any] | None:
        with self._lock, self.connect() as con:
            row = con.execute(
                "SELECT n.*, c.title AS category_title FROM notes n LEFT JOIN categories c ON c.id=n.category_id WHERE n.id = ?",
                (note_id,),
            ).fetchone()
        return self.row_to_note(row) if row else None

    def find_by_title(self, title: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as con:
            row = con.execute(
                "SELECT n.*, c.title AS category_title FROM notes n LEFT JOIN categories c ON c.id=n.category_id "
                "WHERE lower(n.title) = lower(?) LIMIT 1",
                (title.strip(),),
            ).fetchone()
        return self.row_to_note(row) if row else None

    def create_note(
        self,
        title: str,
        content: str = "",
        tags: list[str] | None = None,
        category_id: int | None = None,
    ) -> dict[str, Any]:
        title = title.strip() or "Без названия"
        category_id = category_id if category_id and self.get_category(category_id) else None
        now = self.now()
        with self._lock, self.connect() as con:
            cur = con.execute(
                "INSERT INTO notes(title, content, tags, favorite, category_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (title, content, json.dumps(tags or [], ensure_ascii=False), 0, category_id, now, now),
            )
            note_id = int(cur.lastrowid)
        return self.get_note(note_id) or {}

    def update_note(self, note_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"title", "content", "tags", "favorite", "category_id"}
        values: list[Any] = []
        sets: list[str] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "tags":
                value = json.dumps(value or [], ensure_ascii=False)
            elif key == "favorite":
                value = 1 if value else 0
            elif key == "category_id":
                value = value if value and self.get_category(int(value)) else None
            elif value is None:
                continue
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return self.get_note(note_id)
        sets.append("updated_at = ?")
        values.append(self.now())
        values.append(note_id)
        with self._lock, self.connect() as con:
            cur = con.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", values)
            if cur.rowcount == 0:
                return None
        return self.get_note(note_id)

    def delete_note(self, note_id: int) -> bool:
        with self._lock, self.connect() as con:
            cur = con.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0

    # Categories ---------------------------------------------------------
    def list_categories(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as con:
            rows = con.execute(
                """
                SELECT c.*,
                       (SELECT COUNT(*) FROM notes n WHERE n.category_id=c.id) AS direct_count
                FROM categories c
                ORDER BY c.sort_order, lower(c.title)
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_category(self, category_id: int) -> dict[str, Any] | None:
        with self._lock, self.connect() as con:
            row = con.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        return dict(row) if row else None

    def create_category(self, title: str, parent_id: int | None = None) -> dict[str, Any]:
        title = title.strip() or "Новая категория"
        parent_id = parent_id if parent_id and self.get_category(parent_id) else None
        now = self.now()
        with self._lock, self.connect() as con:
            order = int(
                con.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories WHERE parent_id IS ?",
                    (parent_id,),
                ).fetchone()[0]
            )
            cur = con.execute(
                "INSERT INTO categories(title,parent_id,sort_order,created_at,updated_at) VALUES(?,?,?,?,?)",
                (title, parent_id, order, now, now),
            )
            category_id = int(cur.lastrowid)
        return self.get_category(category_id) or {}

    def update_category(self, category_id: int, title: str | None = None, parent_id: int | None = None, set_parent: bool = False) -> dict[str, Any] | None:
        current = self.get_category(category_id)
        if not current:
            return None
        fields: list[str] = []
        values: list[Any] = []
        if title is not None:
            fields.append("title=?")
            values.append(title.strip() or current["title"])
        if set_parent:
            if parent_id == category_id:
                raise ValueError("Категория не может находиться внутри самой себя")
            new_parent = parent_id if parent_id and self.get_category(parent_id) else None
            if new_parent:
                descendants = set(self.descendant_category_ids(category_id))
                if new_parent in descendants:
                    raise ValueError("Нельзя переместить категорию в её подкатегорию")
            fields.append("parent_id=?")
            values.append(new_parent)
        if not fields:
            return current
        fields.append("updated_at=?")
        values.extend([self.now(), category_id])
        with self._lock, self.connect() as con:
            con.execute(f"UPDATE categories SET {', '.join(fields)} WHERE id=?", values)
        return self.get_category(category_id)

    def delete_category(self, category_id: int) -> bool:
        category = self.get_category(category_id)
        if not category:
            return False
        parent_id = category.get("parent_id")
        with self._lock, self.connect() as con:
            con.execute("UPDATE notes SET category_id=? WHERE category_id=?", (parent_id, category_id))
            con.execute("UPDATE categories SET parent_id=? WHERE parent_id=?", (parent_id, category_id))
            cur = con.execute("DELETE FROM categories WHERE id=?", (category_id,))
        return cur.rowcount > 0

    # Typed relations ----------------------------------------------------
    def add_relation(self, source_id: int, target_id: int, relation_type: str) -> dict[str, Any]:
        if source_id == target_id:
            raise ValueError("Нельзя связать заметку саму с собой")
        if not self.get_note(source_id) or not self.get_note(target_id):
            raise ValueError("Одна из заметок не найдена")
        relation_type = relation_type.strip() or "связано с"
        with self._lock, self.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO relations(source_id,target_id,relation_type,created_at) VALUES(?,?,?,?)",
                (source_id, target_id, relation_type, self.now()),
            )
            row = con.execute(
                "SELECT r.*, s.title AS source_title, t.title AS target_title FROM relations r "
                "JOIN notes s ON s.id=r.source_id JOIN notes t ON t.id=r.target_id "
                "WHERE r.source_id=? AND r.target_id=? AND r.relation_type=?",
                (source_id, target_id, relation_type),
            ).fetchone()
        return dict(row) if row else {}

    def relations_for_note(self, note_id: int) -> list[dict[str, Any]]:
        with self._lock, self.connect() as con:
            rows = con.execute(
                """
                SELECT r.*, s.title AS source_title, t.title AS target_title
                FROM relations r
                JOIN notes s ON s.id=r.source_id
                JOIN notes t ON t.id=r.target_id
                WHERE r.source_id=? OR r.target_id=?
                ORDER BY r.created_at DESC
                """,
                (note_id, note_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_relations(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as con:
            rows = con.execute("SELECT * FROM relations ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def delete_relation(self, relation_id: int) -> bool:
        with self._lock, self.connect() as con:
            cur = con.execute("DELETE FROM relations WHERE id=?", (relation_id,))
        return cur.rowcount > 0

    # Feedback and verified answers -------------------------------------
    def add_feedback(
        self,
        kind: str,
        rating: int,
        query: str = "",
        source_note_id: int | None = None,
        target_note_id: int | None = None,
    ) -> dict[str, Any]:
        rating = 1 if rating > 0 else -1
        with self._lock, self.connect() as con:
            cur = con.execute(
                "INSERT INTO feedback(kind,query,source_note_id,target_note_id,rating,created_at) VALUES(?,?,?,?,?,?)",
                (kind, query.strip(), source_note_id, target_note_id, rating, self.now()),
            )
            feedback_id = int(cur.lastrowid)
        return {"id": feedback_id, "kind": kind, "rating": rating}

    def feedback_bonus(self, query: str, target_note_id: int) -> float:
        with self._lock, self.connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(rating),0) FROM feedback WHERE lower(query)=lower(?) AND target_note_id=?",
                (query.strip(), target_note_id),
            ).fetchone()
        return max(-0.25, min(0.25, float(row[0]) * 0.07))

    def list_answers(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as con:
            rows = con.execute("SELECT * FROM verified_answers ORDER BY updated_at DESC").fetchall()
        return [self.row_to_answer(row) for row in rows]

    def create_answer(self, question: str, answer: str, source_note_ids: list[int] | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        now = self.now()
        with self._lock, self.connect() as con:
            cur = con.execute(
                "INSERT INTO verified_answers(question,answer,source_note_ids,tags,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (
                    question.strip(),
                    answer.strip(),
                    json.dumps(source_note_ids or []),
                    json.dumps(tags or [], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            answer_id = int(cur.lastrowid)
            row = con.execute("SELECT * FROM verified_answers WHERE id=?", (answer_id,)).fetchone()
        return self.row_to_answer(row)

    def delete_answer(self, answer_id: int) -> bool:
        with self._lock, self.connect() as con:
            cur = con.execute("DELETE FROM verified_answers WHERE id=?", (answer_id,))
        return cur.rowcount > 0

    # Settings -----------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock, self.connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self.connect() as con:
            con.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def all_settings(self) -> dict[str, str]:
        with self._lock, self.connect() as con:
            rows = con.execute("SELECT key, value FROM settings").fetchall()
        return {str(row[0]): str(row[1]) for row in rows}
