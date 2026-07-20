"""Persistent identity, visual datasets and household object memory."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any


MEMORY_ROOT = Path("/data/sokol_memory")
MEMORY_SEED_ROOT = Path(__file__).with_name("memory_seed")
MEMORY_DB_PATH = MEMORY_ROOT / "world.db"

DATASET_DIRECTORIES = (
    "identity",
    "people",
    "places",
    "artifacts",
    "vision/people/viktor",
    "vision/people/wife",
    "vision/people/daughter",
    "vision/people/guests",
    "vision/places/bedroom",
    "vision/places/office",
    "vision/places/nursery",
    "vision/places/living_room",
    "vision/places/main_entrance",
    "vision/places/corridor",
    "vision/places/kitchen",
    "vision/artifacts",
    "summaries",
    "exports",
)


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class WorldMemory:
    """Current household state plus an append-only observation history."""

    def __init__(
        self,
        root: Path = MEMORY_ROOT,
        database: Path | None = None,
        seed_root: Path = MEMORY_SEED_ROOT,
    ) -> None:
        self.root = root
        self.database = database or root / "world.db"
        self.seed_root = seed_root
        self._prepare_layout()
        self._initialize_database()

    def _prepare_layout(self) -> None:
        for relative in DATASET_DIRECTORIES:
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        if not self.seed_root.exists():
            return
        for source in self.seed_root.rglob("*"):
            if not source.is_file() or source.name == ".gitkeep":
                continue
            relative = source.relative_to(self.seed_root)
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_database(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL DEFAULT '',
                    place TEXT NOT NULL DEFAULT '',
                    position TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id INTEGER NOT NULL REFERENCES artifacts(artifact_id),
                    observed_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    place TEXT NOT NULL DEFAULT '',
                    position TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS artifact_observations_artifact_at
                    ON artifact_observations(artifact_id, observed_at DESC);
                CREATE INDEX IF NOT EXISTS artifact_observations_event_at
                    ON artifact_observations(event, observed_at DESC);
                """
            )

    @staticmethod
    def _normalize_name(name: str) -> str:
        return " ".join(name.casefold().replace("ё", "е").split())

    @staticmethod
    def _artifact(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["artifact_id"],
            "name": row["name"],
            "kind": row["kind"],
            "place": row["place"],
            "position": row["position"],
            "state": row["state"],
            "confidence": row["confidence"],
            "source": row["source"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
        }

    def remember_artifact(
        self,
        name: str,
        place: str,
        position: str = "",
        kind: str = "",
        state: str = "",
        confidence: float = 1.0,
        source: str = "user",
        note: str = "",
    ) -> str:
        name = " ".join(name.split())
        place = " ".join(place.split())
        if not name or not place:
            raise ValueError("artifact name and place are required")
        normalized = self._normalize_name(name)
        confidence = max(0.0, min(1.0, float(confidence)))
        observed_at = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            previous = connection.execute(
                "SELECT * FROM artifacts WHERE normalized_name=?", (normalized,)
            ).fetchone()
            if previous is None:
                event = "discovered"
                cursor = connection.execute(
                    "INSERT INTO artifacts(name,normalized_name,kind,place,position,state,"
                    "confidence,source,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (name, normalized, kind, place, position, state, confidence, source,
                     observed_at, observed_at),
                )
                artifact_id = int(cursor.lastrowid)
            else:
                artifact_id = int(previous["artifact_id"])
                event = "observed"
                if previous["place"] and previous["place"].casefold() != place.casefold():
                    event = "moved"
                elif position and previous["position"].casefold() != position.casefold():
                    event = "repositioned"
                connection.execute(
                    "UPDATE artifacts SET name=?,kind=?,place=?,position=?,state=?,confidence=?,"
                    "source=?,last_seen_at=? WHERE artifact_id=?",
                    (name, kind or previous["kind"], place,
                     position or previous["position"], state or previous["state"],
                     confidence, source, observed_at, artifact_id),
                )
            connection.execute(
                "INSERT INTO artifact_observations(artifact_id,observed_at,event,place,position,"
                "state,confidence,source,note) VALUES(?,?,?,?,?,?,?,?,?)",
                (artifact_id, observed_at, event, place, position, state, confidence, source, note),
            )
            current = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
        result = self._artifact(current)
        result["event"] = event
        return _compact(result)

    def find_artifacts(self, query: str = "", limit: int = 10) -> str:
        limit = max(1, min(int(limit), 30))
        needle = self._normalize_name(query)
        with closing(self._connect()) as connection:
            if needle:
                rows = connection.execute(
                    "SELECT * FROM artifacts WHERE normalized_name LIKE ? OR lower(kind) LIKE ? "
                    "ORDER BY last_seen_at DESC LIMIT ?",
                    (f"%{needle}%", f"%{needle}%", limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM artifacts ORDER BY last_seen_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return _compact([self._artifact(row) for row in rows])

    def recent_artifact_changes(self, limit: int = 10) -> str:
        limit = max(1, min(int(limit), 30))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT o.observed_at,o.event,a.name,o.place,o.position,o.state,o.confidence,"
                "o.source,o.note FROM artifact_observations o JOIN artifacts a USING(artifact_id) "
                "WHERE o.event IN ('discovered','moved','repositioned') "
                "ORDER BY o.observation_id DESC LIMIT ?", (limit,),
            ).fetchall()
        return _compact([dict(row) for row in rows])

    def prompt_context(self) -> str:
        sections: list[str] = []
        for relative in (
            "identity/personality.md",
            "people/household.json",
            "places/home.json",
            "artifacts/catalog.json",
        ):
            path = self.root / relative
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if content:
                sections.append(f"[{relative}]\n{content}")
        changes = self.recent_artifact_changes(8)
        if changes != "[]":
            sections.append(f"[recent_artifact_changes]\n{changes}")
        return "\n\n".join(sections)
