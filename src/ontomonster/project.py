# SPDX-License-Identifier: BSD-3-Clause
"""Project model for OntoMonster.

A Project wraps a root folder. In the main flow of the app, the root
folder is the Internal Ontology Location. The app builds the Project
with an empty config.

The methods create and open come from the earlier project model. They
use a file named project.toml. This file holds the name, slug, prefix
and namespace. The main flow of the app does not use these methods. The
earlier layout is:

    <parent>/<slug>/
        ttl/            inputs (workspaces to load)
        ontologies/     mid-level reference ontologies
        save/           Parquet version history
        project.toml    name, slug, prefix, namespace
"""

from __future__ import annotations

import re
import datetime as _dt
from pathlib import Path

try:
    # Python 3.11 and later include tomllib. The code only reads with
    # it. The code writes TOML by hand below.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def _toml_dumps(cfg: dict) -> str:
    """Convert a flat config of str, int and bool values to TOML text.
    This needs no third-party writer. This keeps the frozen executable
    small.
    """
    lines = []
    for k, v in cfg.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, int):
            lines.append(f"{k} = {v}")
        else:
            s = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{s}"')
    return "\n".join(lines) + "\n"


SCHEMA_VERSION = 1
SUBDIRS = ("ttl", "ontologies", "save", "refcache")
DEFAULT_NS_BASE = "http://example.org/"


def slugify(name: str) -> str:
    """Return a slug that is safe for file names and IRIs. It has
    lowercase letters and digits, separated by hyphens.
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "project"


def default_namespace(name: str) -> str:
    """Return a suggested hash-style namespace for a project name."""
    return f"{DEFAULT_NS_BASE}{slugify(name)}#"


def normalize_namespace(ns: str, name: str) -> str:
    """Use the IRI that the user gives. Make it hash-style: add # if it
    is missing.
    """
    ns = (ns or "").strip()
    if not ns:
        return default_namespace(name)
    if not (ns.endswith("#") or ns.endswith("/")):
        ns += "#"
    return ns


class ProjectError(Exception):
    pass


class Project:
    def __init__(self, root: Path, config: dict):
        self.root = Path(root)
        self.config = config

    # Identity and configuration.
    @property
    def name(self) -> str:
        return self.config.get("name", self.root.name)

    @property
    def slug(self) -> str:
        return self.config.get("slug", self.root.name)

    @property
    def prefix(self) -> str:
        return self.config.get("prefix", self.slug)

    @property
    def namespace(self) -> str:
        return self.config.get("namespace", default_namespace(self.name))

    @property
    def base_iri(self) -> str:
        """Return the namespace without the final # or /. This is the
        stem of the ontology IRI.
        """
        return self.namespace.rstrip("#/")

    def set_identity(
        self, prefix: str | None = None, namespace: str | None = None
    ) -> "Project":
        """Change the prefix or the namespace of the project and save
        project.toml.

        The namespace is the single IRI base for every term that an
        export creates, in all workspaces. The prefix is the short label
        of the namespace. The method changes the namespace to hash
        style. If the prefix is blank, the method uses the slug. The
        method changes only the fields that the caller gives.
        """
        if namespace is not None:
            self.config["namespace"] = normalize_namespace(
                namespace, self.name
            )
        if prefix is not None:
            self.config["prefix"] = (prefix or "").strip() or self.slug
        (self.root / "project.toml").write_bytes(
            _toml_dumps(self.config).encode("utf-8")
        )
        return self

    @property
    def ttl_dir(self) -> Path:
        return self.root / "ttl"

    @property
    def ontologies_dir(self) -> Path:
        return self.root / "ontologies"

    @property
    def save_dir(self) -> Path:
        return self.root / "save"

    @property
    def refcache_dir(self) -> Path:
        return self.root / "refcache"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "prefix": self.prefix,
            "namespace": self.namespace,
            "base_iri": self.base_iri,
            "root": str(self.root),
        }

    # Creation and loading.
    @classmethod
    def create(
        cls,
        parent,
        name: str,
        prefix: str | None = None,
        namespace: str | None = None,
    ) -> "Project":
        parent = Path(parent).expanduser()
        if not parent.is_dir():
            raise ProjectError(f"Parent folder does not exist: {parent}")
        slug = slugify(name)
        root = parent / slug
        if root.exists():
            raise ProjectError(f"A folder named '{slug}' already exists here.")
        ns = normalize_namespace(namespace, name)
        pfx = (prefix or slug).strip() or slug
        for sub in SUBDIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        cfg = {
            "name": name.strip() or slug,
            "slug": slug,
            "prefix": pfx,
            "namespace": ns,
            "schema_version": SCHEMA_VERSION,
            "created": _dt.date.today().isoformat(),
        }
        (root / "project.toml").write_bytes(_toml_dumps(cfg).encode("utf-8"))
        print(f"[project] created {root}  (prefix={pfx}, namespace={ns})")
        return cls(root, cfg)

    @classmethod
    def open(cls, location) -> "Project":
        if not location or not str(location).strip():
            raise ProjectError("No project folder was provided.")
        p = Path(location).expanduser()
        # Accept a project folder, a project.toml file, or any file or
        # subfolder in a project. Go up from the given path to the
        # nearest project.toml. This way a pick that is slightly wrong
        # still opens.
        if p.name == "project.toml" and p.is_file():
            root = p.parent
        else:
            probe = p if p.is_dir() else p.parent
            root = None
            for _ in range(64):
                if (probe / "project.toml").is_file():
                    root = probe
                    break
                if probe.parent == probe:
                    break
                probe = probe.parent
        if root is None:
            raise ProjectError(
                f"Not an OntoMonster project: no project.toml at or above "
                f"{p}. "
                "Pick the project folder itself (the one that contains "
                "project.toml)."
            )
        cfg = tomllib.loads(
            (root / "project.toml").read_text(encoding="utf-8")
        )
        # Create each missing structure folder. Do not change any
        # content.
        for sub in SUBDIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        print(f"[project] opened {root}  (prefix={cfg.get('prefix')})")
        return cls(root, cfg)

    @classmethod
    def is_project(cls, location) -> bool:
        return (Path(location).expanduser() / "project.toml").is_file()
