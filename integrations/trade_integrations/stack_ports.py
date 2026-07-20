"""Load canonical stack ports from stack/ports.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_PORTS_REL = Path("stack") / "ports.yaml"


def trade_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ports_yaml_path(*, root: Path | None = None) -> Path:
    return (root or trade_repo_root()) / _PORTS_REL


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to read stack/ports.yaml — run: pip install pyyaml"
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid ports yaml: {path}")
    return data


def _format_value(template: str, *, host: str, browser_host: str, host_port: int, container_port: int | None) -> str:
    return template.format(
        host=host,
        browser_host=browser_host,
        host_port=host_port,
        container_port=container_port or host_port,
    )


@lru_cache(maxsize=1)
def load_ports_registry(*, root: str | None = None) -> dict[str, Any]:
    base = Path(root) if root else trade_repo_root()
    raw = _load_yaml(ports_yaml_path(root=base))
    meta = raw.get("meta") or {}
    host = str(meta.get("host") or "127.0.0.1")
    browser_host = str(meta.get("browser_host") or "localhost")
    services_in = raw.get("services") or {}
    services: dict[str, dict[str, Any]] = {}
    for name, spec in services_in.items():
        if not isinstance(spec, dict):
            continue
        host_port = int(spec["host_port"])
        container_port = spec.get("container_port")
        container_port_int = int(container_port) if container_port is not None else None
        ctx = {
            "host": host,
            "browser_host": browser_host,
            "host_port": host_port,
            "container_port": container_port_int,
        }
        env_out: dict[str, str] = {}
        for key, tmpl in (spec.get("env") or {}).items():
            env_out[str(key)] = _format_value(str(tmpl), **ctx)
        url_out: dict[str, str] = {}
        for key, tmpl in (spec.get("urls") or {}).items():
            url_out[str(key)] = _format_value(str(tmpl), **ctx)
        docker_publish = None
        docker = spec.get("docker") or {}
        if docker.get("publish"):
            docker_publish = _format_value(str(docker["publish"]), **ctx)
        services[str(name)] = {
            "description": spec.get("description") or "",
            "host_port": host_port,
            "container_port": container_port_int,
            "docker_publish": docker_publish,
            "env": env_out,
            "urls": url_out,
            "notes": spec.get("notes") or "",
        }
    return {"meta": {"host": host, "browser_host": browser_host}, "services": services}


def build_env_map(*, root: Path | None = None) -> dict[str, str]:
    reg = load_ports_registry(root=str(root) if root else None)
    out: dict[str, str] = {}
    for spec in reg["services"].values():
        out.update(spec.get("env") or {})
        out.update(spec.get("urls") or {})
    return out


def validate_ports(*, root: Path | None = None) -> list[str]:
    reg = load_ports_registry(root=str(root) if root else None)
    errors: list[str] = []
    seen: dict[int, list[str]] = {}
    for name, spec in reg["services"].items():
        port = int(spec["host_port"])
        seen.setdefault(port, []).append(name)
    for port, names in sorted(seen.items()):
        if len(names) > 1:
            errors.append(f"host port {port} used by multiple services: {', '.join(names)}")
    zmq = reg["services"].get("openalgo_zmq", {}).get("host_port")
    searxng = reg["services"].get("searxng", {}).get("host_port")
    if zmq is not None and searxng is not None and int(zmq) == int(searxng):
        errors.append("openalgo_zmq and searxng must not share the same host port")
    return errors


def _listener_pids(port: int) -> list[int]:
    import subprocess

    try:
        out = subprocess.run(
            ["lsof", "-t", "-iTCP:%d" % port, "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    pids: list[int] = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _claimed_pids(root: Path | None) -> set[int]:
    if root is None:
        return set()
    claims_dir = root / "log" / "claims"
    pids: set[int] = set()
    if not claims_dir.is_dir():
        return pids
    for claim in claims_dir.glob("*.claim"):
        try:
            for line in claim.read_text(encoding="utf-8").splitlines():
                if line.startswith("pid="):
                    pids.add(int(line.split("=", 1)[1].strip()))
        except (OSError, ValueError):
            continue
    return pids


def check_port_listeners(*, root: Path | None = None, allow_pids: set[int] | None = None) -> list[str]:
    """Return errors when registry host ports are held by unexpected listeners."""
    reg = load_ports_registry(root=str(root) if root else None)
    root_path = root.resolve() if root else None
    allow = set(allow_pids or set()) | _claimed_pids(root)
    errors: list[str] = []
    import subprocess

    for name, spec in reg["services"].items():
        port = int(spec["host_port"])
        pids = _listener_pids(port)
        if not pids:
            continue
        for pid in pids:
            if pid in allow:
                continue
            try:
                ps = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "comm="],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                comm = (ps.stdout or "").strip()
                comm_base = comm.rsplit("/", 1)[-1]
            except OSError:
                comm = "?"
                comm_base = comm
            # Docker-published ports show as com.docker.backend / docker-proxy on macOS/Linux.
            if comm_base in {"com.docker.backend", "docker-proxy", "Docker", "docker"}:
                continue
            try:
                full = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "args="],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                args = (full.stdout or "").strip()
            except OSError:
                args = comm_base
            if root_path and str(root_path) in args:
                continue
            if "docker" in args.lower() and "compose" in args.lower():
                continue
            errors.append(
                f"{name} needs port {port} but pid {pid} ({comm_base or 'unknown'}) is listening"
            )
            break
    return errors


def all_host_ports(*, root: Path | None = None) -> dict[str, int]:
    reg = load_ports_registry(root=str(root) if root else None)
    return {name: int(spec["host_port"]) for name, spec in reg["services"].items()}


def env_or_default(key: str, *, root: Path | None = None) -> str:
    val = os.getenv(key)
    if val:
        return val.strip()
    env_map = build_env_map(root=root)
    if key in env_map:
        return env_map[key]
    raise KeyError(f"Unknown stack port env key: {key}")


def service_host_port(name: str, *, root: Path | None = None) -> int:
    reg = load_ports_registry(root=str(root) if root else None)
    spec = reg["services"].get(name)
    if not spec:
        raise KeyError(name)
    return int(spec["host_port"])


def openalgo_host(*, root: Path | None = None) -> str:
    return env_or_default("OPENALGO_HOST", root=root).rstrip("/")


def vibe_backend_url(*, root: Path | None = None) -> str:
    return env_or_default("VIBE_BACKEND_URL", root=root).rstrip("/")


def vibe_frontend_url(*, root: Path | None = None) -> str:
    return env_or_default("VIBE_FRONTEND_URL", root=root).rstrip("/")


def searxng_base_url(*, root: Path | None = None) -> str:
    return env_or_default("SEARXNG_BASE_URL", root=root).rstrip("/")


def timescale_database_url(*, root: Path | None = None) -> str:
    return env_or_default("TIMESCALE_DATABASE_URL", root=root)


def nautilus_redis_url(*, root: Path | None = None) -> str:
    return env_or_default("NAUTILUS_REDIS_URL", root=root)


def clear_cache() -> None:
    load_ports_registry.cache_clear()
