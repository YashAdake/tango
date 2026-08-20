"""Conversation memory — what "it" refers to.

People do not repeat themselves. After *"start optiresume"* they say *"actually
kill it"*; after a failed check they say *"why"*. A system that cannot follow
that forces you to talk like a command line, which is the opposite of the point.

**The design constraint that shapes this module: every CLI invocation is a new
process.** Session state held in memory would be born empty every time, so
"conversation memory" would silently mean "no memory" — the same class of defect
as an undo window that only exists until the process exits. It is persisted.

Sticky trust labels live here too (docs/17 H3). A session that has read
untrusted content stays untrusted for its remaining turns: otherwise *"read this
page"* followed by *"now email Rahul"* launders the first turn through the
second.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tango.store import Store, utcnow
from tango.types import Integrity, TaskStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id                 TEXT PRIMARY KEY,
    surface            TEXT NOT NULL,
    last_project       TEXT,
    last_playbook      TEXT,
    last_task_id       TEXT,
    last_status        TEXT,
    last_target        TEXT,
    last_utterance     TEXT,
    max_integrity      INTEGER NOT NULL DEFAULT 0,
    untrusted_sources  TEXT,
    turns              INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_surface ON session(surface, updated_at);
"""

# After this long, "it" no longer means what it meant. Resolving a pronoun
# against something from yesterday is worse than asking.
IDLE_TIMEOUT = timedelta(minutes=30)


@dataclass
class Session:
    """One conversational thread on one surface."""

    id: str
    surface: str
    last_project: str | None = None
    last_playbook: str | None = None
    last_task_id: str | None = None
    last_status: str | None = None
    last_target: str | None = None
    last_utterance: str | None = None
    max_integrity: Integrity = Integrity.TRUSTED
    untrusted_sources: tuple[str, ...] = ()
    turns: int = 0
    updated_at: str = ""

    @property
    def is_stale(self) -> bool:
        if not self.updated_at:
            return True
        return datetime.fromisoformat(self.updated_at) + IDLE_TIMEOUT < datetime.now(UTC)

    @property
    def last_failed(self) -> bool:
        return self.last_status in (TaskStatus.FAILED, TaskStatus.PARTIAL)

    def as_context(self) -> dict[str, Any]:
        """The shape the router reads.

        A stale session contributes nothing rather than a wrong referent —
        silence is a better answer than confidently resolving "it" to something
        from an hour ago.
        """
        if self.is_stale:
            return {}
        context: dict[str, Any] = {}
        if self.last_project:
            context["prior_project"] = self.last_project
        if self.last_playbook and self.last_project:
            context["last_action"] = f"{self.last_playbook} {self.last_project}"
        if self.last_target:
            context["last_target"] = self.last_target
        if self.last_failed:
            context["last_task"] = f"{self.last_playbook} {self.last_status}"
            context["last_failure"] = self.last_target or self.last_project or "*"
        context["max_integrity"] = int(self.max_integrity)
        return context


class SessionStore:
    """Persisted conversation state, one row per surface."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.store.conn.executescript(SCHEMA)

    def current(self, surface: str = "cli") -> Session:
        """The live session for a surface, or a fresh one.

        Each surface gets its own thread: what you said to the CLI should not
        silently become the referent for something you say on your phone
        (docs/07 §1.5).
        """
        row = self.store.conn.execute(
            "SELECT * FROM session WHERE surface=? ORDER BY updated_at DESC LIMIT 1",
            (surface,),
        ).fetchone()
        if row is None:
            return self._create(surface)

        session = self._from_row(row)
        if session.is_stale:
            return self._create(surface)
        return session

    def _create(self, surface: str) -> Session:
        session = Session(id=str(uuid.uuid4()), surface=surface, updated_at=utcnow())
        self.store.conn.execute(
            "INSERT INTO session(id, surface, max_integrity, untrusted_sources, turns,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (session.id, surface, 0, "[]", 0, session.updated_at, session.updated_at),
        )
        return session

    def record(
        self,
        session: Session,
        *,
        utterance: str,
        playbook: str | None = None,
        project: str | None = None,
        target: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
    ) -> Session:
        """Remember this turn.

        Only overwrites what the turn actually established: asking "is prod ok"
        after "start myjson" should not erase myjson as the referent for "it".
        """
        session.last_utterance = utterance
        session.turns += 1
        if playbook:
            session.last_playbook = playbook
        if project:
            session.last_project = project
        if target:
            session.last_target = target
        if task_id:
            session.last_task_id = task_id
        if status:
            session.last_status = status
        session.updated_at = utcnow()

        self.store.conn.execute(
            "UPDATE session SET last_project=?, last_playbook=?, last_task_id=?,"
            " last_status=?, last_target=?, last_utterance=?, turns=?, updated_at=?"
            " WHERE id=?",
            (session.last_project, session.last_playbook, session.last_task_id,
             session.last_status, session.last_target, session.last_utterance,
             session.turns, session.updated_at, session.id),
        )
        return session

    def observe(self, session: Session, integrity: Integrity, source: str) -> Session:
        """Record untrusted content reaching this conversation.

        Sticky and monotonic: once a session has read something hostile, later
        turns inherit that, because "read this page" then "now email Rahul" is
        one conversation and the attacker wrote half of it.
        """
        session.max_integrity = Integrity(max(session.max_integrity, integrity))
        if integrity is Integrity.UNTRUSTED and source:
            session.untrusted_sources = (*session.untrusted_sources, source)
        self.store.conn.execute(
            "UPDATE session SET max_integrity=?, untrusted_sources=?, updated_at=?"
            " WHERE id=?",
            (int(session.max_integrity), json.dumps(list(session.untrusted_sources)),
             utcnow(), session.id),
        )
        return session

    def end(self, surface: str = "cli") -> None:
        """Close the thread. "Thanks Tango" — the referents go with it."""
        self.store.conn.execute(
            "UPDATE session SET updated_at=? WHERE surface=?",
            ((datetime.now(UTC) - IDLE_TIMEOUT - timedelta(seconds=1)).isoformat(), surface),
        )

    @staticmethod
    def _from_row(row: Any) -> Session:
        return Session(
            id=row["id"], surface=row["surface"], last_project=row["last_project"],
            last_playbook=row["last_playbook"], last_task_id=row["last_task_id"],
            last_status=row["last_status"], last_target=row["last_target"],
            last_utterance=row["last_utterance"],
            max_integrity=Integrity(row["max_integrity"]),
            untrusted_sources=tuple(json.loads(row["untrusted_sources"] or "[]")),
            turns=row["turns"], updated_at=row["updated_at"],
        )


# ------------------------------------------------------------- placeholders

# Golden-set rows use these to mean "whatever the conversation established".
# Resolving them is what turns a command line into a conversation.
PLACEHOLDERS = {
    "@running": "last_project",
    "@last_api": "last_target",
    "@last_failure": "last_failure",
    "@last_deployed": "last_project",
}


def resolve_placeholders(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Replace ``@placeholder`` values with what the conversation established.

    An unresolvable placeholder stays as it is rather than becoming a guess —
    the caller then asks, which is the correct behaviour when "it" has no
    referent.
    """
    resolved = dict(params)
    for key, value in params.items():
        if not isinstance(value, str) or not value.startswith("@"):
            continue
        source = PLACEHOLDERS.get(value)
        if source and context.get(source):
            resolved[key] = context[source]
    return resolved
