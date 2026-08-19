"""Project registry and deterministic resolvers.

Tango's model of your world. Host-aware from the start (docs/16 §14.1): the
development machine and the lab laptop see different paths, and retrofitting
that later is far more painful than carrying it from the first commit.

Resolvers are the mechanism behind ADR-009: a model never authors a project
name, a path, or a container. It picks an ID from an enumerated, ranked list, or
it asks. Zero candidates is a hard failure — never a guess.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Project:
    id: str
    path: str
    stack: str = ""
    aliases: tuple[str, ...] = ()
    dev_cmd: str | None = None
    dev_cwd: str | None = None
    health_url: str | None = None
    prod_url: str | None = None
    compose_path: str | None = None
    compose_service: str | None = None
    container: str | None = None
    deploy_branch: str = "main"
    editor_path: str | None = None

    @property
    def exists(self) -> bool:
        return Path(self.path).is_dir()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "path": self.path, "stack": self.stack,
            "health_url": self.health_url, "prod_url": self.prod_url,
            "container": self.container, "deploy_branch": self.deploy_branch,
        }


@dataclass(frozen=True)
class Candidate:
    """One possible resolution, with a score. The model may choose among these;
    it may not invent one."""

    id: str
    display: str
    confidence: float
    project: Project


class ResolutionError(RuntimeError):
    """Nothing matched. Deliberately fatal — guessing which project the user
    meant is how you start the wrong stack, or stop the right one."""


class AmbiguousResolution(RuntimeError):
    """Several plausible matches. The caller must ask, not pick."""

    def __init__(self, query: str, candidates: list[Candidate]) -> None:
        names = ", ".join(c.id for c in candidates)
        super().__init__(f"'{query}' could be: {names}")
        self.query = query
        self.candidates = candidates


@dataclass
class ProjectRegistry:
    projects: dict[str, Project] = field(default_factory=dict)
    hostname: str = ""

    @classmethod
    def load(cls, root: Path | str = "hosts", hostname: str | None = None) -> ProjectRegistry:
        """Load ``hosts/<hostname>/projects.json``, falling back to ``default``."""
        host = hostname or os.environ.get("TANGO_HOST") or socket.gethostname().lower()
        base = Path(root)
        for name in (host, "default"):
            candidate = base / name / "projects.json"
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                return cls(
                    projects={
                        p["id"]: Project(
                            id=p["id"],
                            path=p["path"],
                            stack=p.get("stack", ""),
                            aliases=tuple(p.get("aliases", ())),
                            dev_cmd=p.get("dev_cmd"),
                            dev_cwd=p.get("dev_cwd"),
                            health_url=p.get("health_url"),
                            prod_url=p.get("prod_url"),
                            compose_path=p.get("compose_path"),
                            compose_service=p.get("compose_service"),
                            container=p.get("container"),
                            deploy_branch=p.get("deploy_branch", "main"),
                            editor_path=p.get("editor_path"),
                        )
                        for p in data["projects"]
                    },
                    hostname=name,
                )
        return cls(projects={}, hostname=host)

    def get(self, pid: str) -> Project:
        if pid not in self.projects:
            raise ResolutionError(f"no project with id '{pid}'")
        return self.projects[pid]

    def ids(self) -> list[str]:
        return sorted(self.projects)

    # ------------------------------------------------------------- resolution

    def candidates(self, query: str) -> list[Candidate]:
        """Rank projects against a phrase. Deterministic — no model involved."""
        q = query.strip().lower()
        if not q:
            return []

        found: list[Candidate] = []
        for p in self.projects.values():
            names = (p.id.lower(), *(a.lower() for a in p.aliases))
            score = 0.0
            for name in names:
                if q == name:
                    score = max(score, 1.0)
                elif name.startswith(q) or q.startswith(name):
                    score = max(score, 0.8)
                elif q in name or name in q:
                    score = max(score, 0.6)
                elif set(q.split()) & set(name.replace("-", " ").replace("_", " ").split()):
                    score = max(score, 0.4)
            if score > 0:
                found.append(Candidate(p.id, p.id, score, p))
        return sorted(found, key=lambda c: (-c.confidence, c.id))

    def resolve(self, query: str) -> Project:
        """Resolve a phrase to exactly one project, or refuse.

        One high-confidence match wins. Several close matches raise, so the
        caller asks. Nothing matching raises. There is no fourth branch where a
        guess is returned.
        """
        ranked = self.candidates(query)
        if not ranked:
            known = ", ".join(self.ids()) or "none configured"
            raise ResolutionError(f"I don't know a project called '{query}'. I know: {known}")

        best = ranked[0]
        rivals = [c for c in ranked[1:] if c.confidence >= best.confidence - 0.01]
        if rivals and best.confidence < 1.0:
            raise AmbiguousResolution(query, [best, *rivals])
        return best.project
