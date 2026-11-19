#!/usr/bin/env python3

# Standard-library modules for the CLI, SQLite storage, and filesystem paths.
import argparse
import sqlite3
from pathlib import Path


# Keep runtime files beside the repository, regardless of the caller's cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR_NAME = ".state"
REPORTS_DIR_NAME = "reports"
DATABASE_NAME = "stock_market_watcher.db"


# Create the tables used for companies, data sources, price snapshots, and news.
SCHEMA = """
-- Store each tracked company and its unique ticker.
CREATE TABLE IF NOT EXISTS companies (
    company_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ticker TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Record the public sources used to retrieve prices or news.
CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE
);

-- Keep timestamped price observations linked to their company and source.
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(company_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

-- Associate published news items with the snapshot and source they support.
CREATE TABLE IF NOT EXISTS news_items (
    news_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    published_at TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);
"""


def initialize_project(project_root: Path | None = None) -> Path:
    """Create runtime folders and initialize the SQLite database."""
    # Allow tests or callers to target a temporary project directory.
    root = (project_root or PROJECT_ROOT).resolve()
    state_dir = root / STATE_DIR_NAME
    reports_dir = root / REPORTS_DIR_NAME

    # Repeated initialization should leave existing directories and data intact.
    state_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Create missing tables without replacing any existing database contents.
    database_path = state_dir / DATABASE_NAME
    with sqlite3.connect(database_path) as connection:
        # Enforce the foreign-key relationships defined in the schema.
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)

    # Return the database location for the CLI and other callers.
    return database_path


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line arguments accepted by this script."""
    parser = argparse.ArgumentParser(
        description="Initialize and run the stock market watcher."
    )
    parser.add_argument(
        "command",
        choices=("init",),
        help="initialize the .state and reports directories",
    )
    return parser


def main() -> int:
    """Parse the requested command and execute its action."""
    args = build_parser().parse_args()

    if args.command == "init":
        # Initialize the project and report the paths created or reused.
        database_path = initialize_project()
        print(f"Initialized .state/ at {PROJECT_ROOT / STATE_DIR_NAME}")
        print(f"Initialized reports/ at {PROJECT_ROOT / REPORTS_DIR_NAME}")
        print(f"Initialized SQLite database at {database_path}")
        return 0

    # Argparse currently limits commands to init; keep a successful exit fallback.
    return 0


# Run the CLI only when this file is executed directly, not when imported.
if __name__ == "__main__":
    raise SystemExit(main())
