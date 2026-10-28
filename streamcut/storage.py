import json
import sqlite3
from pathlib import Path

from .domain import Project


class ProjectStore:
    """One atomic snapshot per save; WAL recovers the previous complete transaction."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "cache").mkdir(exist_ok=True)
        self.path = self.folder / "project.sqlite"
        with sqlite3.connect(self.path) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError("Проект создан более новой версией StreamCut")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("PRAGMA user_version=1")

    def save(self, project: Project):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO snapshot VALUES (1, ?)", (project.model_dump_json(),))

    def load(self) -> Project:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT data FROM snapshot WHERE id=1").fetchone()
        if row is None:
            raise ValueError("В папке нет сохранённого проекта")
        data = json.loads(row[0])
        if data.get("schema_version", 1) > 1:
            raise ValueError("Неподдерживаемая версия проекта")
        project = Project.model_validate(data)
        for job in project.jobs:
            if job.state == "выполняется":
                job.state = "ожидает"
                job.progress = 0
        return project
