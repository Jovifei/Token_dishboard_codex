"""Bounded local metadata discovery. Never opens message/rollout databases."""
import json
import ntpath
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

from sync import read_file
from usage import session_identity

ALL_PROJECTS = object()


def path_key(value):
    value = str(value).replace("/", "\\")
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


@contextmanager
def metadata_db(database):
    path = Path(database).absolute()
    if not path.is_file() or path.is_symlink():
        raise ValueError("未找到可读的 Codex 本机元数据；不会新建数据库或改读全部聊天正文")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.set_progress_handler(lambda: 1, 2_000_000)
            yield conn, path
    except sqlite3.Error as exc:
        raise ValueError("聊天元数据暂不可读，请稍后刷新；未修改数据库：" + str(exc)) from exc


def native_projects(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    native, roots = {}, {}
    if "projects" not in tables:
        return native, roots
    columns = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    if not {"id", "name"} <= columns:
        raise ValueError("Codex 项目元数据结构不兼容；不会猜测项目归属")
    for identity, name in conn.execute("SELECT id,name FROM projects ORDER BY name,id"):
        native[identity] = {"app_project_id": identity, "label": name or identity, "roots": []}
    if "project_roots" in tables:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(project_roots)")}
        if not {"project_id", "path"} <= columns:
            raise ValueError("Codex 项目目录结构不兼容；不会猜测项目归属")
        for identity, root in conn.execute("SELECT project_id,path FROM project_roots ORDER BY path"):
            if identity in native and root:
                native[identity]["roots"].append(root)
                roots.setdefault(path_key(root), set()).add(identity)
    return native, roots


class ProjectCatalog:
    def __init__(self, conn, database):
        self.source = "SQLite 项目表"
        self.assignments, self.hosts, self.hints, self.aliases = {}, {}, {}, {}
        self.projectless = set()
        state_path = database.with_name(".codex-global-state.json")
        try:
            state = json.loads(read_file(state_path, maximum=16_000_000))
        except FileNotFoundError:
            self.projects, self.roots = native_projects(conn)
            return
        except (OSError, ValueError) as exc:
            raise ValueError("Codex 桌面侧栏元数据暂不可读，请刷新后重试") from exc

        if not isinstance(state, dict):
            raise ValueError("Codex 桌面侧栏元数据格式不兼容")
        order = state.get("project-order")
        entries = state.get("local-projects")
        if order is None and entries is None:
            self.projects, self.roots = native_projects(conn)
            return
        if not isinstance(order, list) or not isinstance(entries, dict):
            raise ValueError("Codex 桌面侧栏项目清单格式不兼容")

        self.source = "Codex 桌面侧栏"
        self.projects, self.roots = {}, {}
        # SQLite may retain stale projects; the desktop's ordered list controls visibility.
        for identity in order:
            if not isinstance(identity, str) or identity in self.projects:
                continue
            entry = entries.get(identity)
            if not isinstance(entry, dict):
                continue
            name, paths = entry.get("name"), entry.get("rootPaths")
            paths = [p for p in paths if isinstance(p, str) and p] if isinstance(paths, list) else []
            self.projects[identity] = {"app_project_id": identity,
                                       "label": name if isinstance(name, str) and name else identity,
                                       "roots": paths}
            for root in paths:
                self.roots.setdefault(path_key(root), set()).add(identity)

        local_host = "local:" + str(database.parent)
        host_aliases = self.state_map(state, "app-server-project-id-by-legacy-project-id-by-host")
        candidates = {}
        for host, aliases in host_aliases.items():
            if path_key(host) != path_key(local_host) or not isinstance(aliases, dict):
                continue
            for identity, canonical in aliases.items():
                if isinstance(canonical, str) and canonical:
                    candidates.setdefault(canonical, set()).add(identity)
        self.aliases = {k: next(iter(v)) for k, v in candidates.items() if len(v) == 1}
        self.assignments = self.state_map(state, "thread-project-assignments")
        self.hosts = self.state_map(state, "thread-project-membership-host-ids")
        self.hints = self.state_map(state, "thread-workspace-root-hints")
        projectless = state.get("projectless-thread-ids", [])
        if not isinstance(projectless, list):
            raise ValueError("Codex 桌面侧栏项目归属格式不兼容")
        self.projectless = {key for key in projectless if isinstance(key, str)}

    @staticmethod
    def state_map(state, key):
        value = state.get(key, {})
        if not isinstance(value, dict):
            raise ValueError("Codex 桌面侧栏项目归属格式不兼容")
        return value

    def membership(self, identity, cwd, sql_project):
        if identity in self.projectless:
            return None, "PROJECTLESS"
        if self.hosts.get(identity) not in (None, "local"):
            return None, "OTHER_HOST"
        if identity in self.assignments:
            assignment = self.assignments[identity]
            if isinstance(assignment, dict) and assignment.get("projectKind") == "local":
                pid = assignment.get("projectId")
                if isinstance(pid, str):
                    pid = self.aliases.get(pid, pid)
                    if pid in self.projects:
                        return pid, "SIDEBAR"
            return None, "UNKNOWN_PROJECT"
        if sql_project:
            pid = self.aliases.get(sql_project, sql_project)
            return (pid, "EXPLICIT") if pid in self.projects else (None, "UNKNOWN_PROJECT")
        hint = self.hints.get(identity)
        if isinstance(hint, str) and hint:
            candidates = self.roots.get(path_key(hint), set())
            if len(candidates) == 1:
                return next(iter(candidates)), "WORKTREE_HINT"
            return None, "AMBIGUOUS_ROOT" if candidates else "UNASSIGNED"
        candidates = self.roots.get(path_key(cwd), set()) if cwd else set()
        if len(candidates) == 1:
            return next(iter(candidates)), "EXACT_ROOT"
        return None, "AMBIGUOUS_ROOT" if candidates else "UNASSIGNED"


def title_expression(columns):
    return "COALESCE(NULLIF(name,''),NULLIF(title,''))" if "title" in columns else "name"


def discover(database, projects, limit=200, *, app_project=ALL_PROJECTS, offset=0):
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 500):
        raise ValueError("聊天列表范围须为 1–500")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("聊天列表起点无效")
    by_path = {}
    for project in projects:
        by_path.setdefault(path_key(project["path"]), []).append(project["project_id"])
    with metadata_db(database) as (conn, path):
        conn.execute("BEGIN")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
        required = {"id", "name", "cwd", "source", "archived", "updated_at"}
        if not required <= columns:
            raise ValueError("Codex 元数据结构不兼容；未回退到旧登记表冒充聊天列表")
        clause = "archived=0 AND source NOT LIKE '%subagent%'"
        if "thread_source" in columns:
            clause += " AND (thread_source='user' OR thread_source IS NULL)"
        if "agent_role" in columns:
            clause += " AND agent_role IS NULL"
        catalog = ProjectCatalog(conn, path)
        member = "project_id" if "project_id" in columns else "NULL"
        recency = "COALESCE(recency_at,updated_at)" if "recency_at" in columns else "updated_at"
        total = conn.execute("SELECT COUNT(*) FROM threads WHERE " + clause).fetchone()[0]
        chats = []
        matched = 0
        has_more = False
        rows = conn.execute(f"SELECT id,{title_expression(columns)},cwd,{recency},{member} "
                            f"FROM threads WHERE {clause} ORDER BY 4 DESC,id")
        for identity, name, cwd, updated, membership in rows:
            ids = by_path.get(path_key(cwd or ""), [])
            app_id, app_mapping = catalog.membership(identity, cwd, membership)
            if app_project is not ALL_PROJECTS and app_id != app_project:
                continue
            if matched < offset:
                matched += 1
                continue
            if len(chats) == limit:
                has_more = True
                break
            chats.append({"chat_id": identity, "label": (name or identity)[:160],
                          "path": cwd or "", "updated_at": updated,
                          "app_project_id": app_id, "app_mapping": app_mapping,
                          "app_project_name": catalog.projects[app_id]["label"] if app_id else None,
                          "project_id": ids[0] if len(ids) == 1 else None,
                          "mapping": "EXACT" if len(ids) == 1 else
                                     ("AMBIGUOUS" if ids else "UNREGISTERED")})
    return {"chats": chats, "app_projects": list(catalog.projects.values()),
            "project_source": catalog.source,
            "total": total, "limit": limit, "offset": offset,
            "has_more": has_more, "source": str(path)}


def matching_rows(conn, statement, identities):
    identities = sorted(identities)
    for offset in range(0, len(identities), 400):
        batch = identities[offset:offset + 400]
        yield from conn.execute(statement.format(",".join("?" for _ in batch)), batch)


def usage_metadata(database, sessions):
    """Join snapshot IDs to the desktop catalog, including archived chats."""
    keys = {key for row in sessions if (key := session_identity(row))}
    if not keys:
        return {}
    children = {key for row in sessions if row.get("role") == "child" and (key := session_identity(row))}
    with metadata_db(database) as (conn, path):
        conn.execute("BEGIN")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
        if not {"id", "name", "cwd", "source"} <= columns:
            raise ValueError("Codex 聊天标题结构不兼容")
        catalog = ProjectCatalog(conn, path)
        edges = {r[1] for r in conn.execute("PRAGMA table_info(thread_spawn_edges)")}
        parents = {}
        if {"child_thread_id", "parent_thread_id"} <= edges:
            for child, parent in matching_rows(conn, "SELECT child_thread_id,parent_thread_id "
                                               "FROM thread_spawn_edges WHERE child_thread_id IN ({})", children):
                if isinstance(parent, str) and parent and parent != child:
                    parents.setdefault(child, set()).add(parent)
        member = "project_id" if "project_id" in columns else "NULL"
        agent_role = "agent_role" if "agent_role" in columns else "NULL"
        thread_source = "thread_source" if "thread_source" in columns else "NULL"
        rows = matching_rows(conn, f"SELECT id,{title_expression(columns)},cwd,{member},source,"
                             f"{agent_role},{thread_source} "
                             "FROM threads WHERE id IN ({})", keys.union(*parents.values()))
        records = {}
        for identity, name, cwd, membership, source, role, origin in rows:
            pid, mapping = catalog.membership(identity, cwd, membership)
            records[identity] = {"project_id": pid,
                                 "project_name": catalog.projects[pid]["label"] if pid else "未归类",
                                 "chat_id": identity, "title": (name or "标题未读到")[:160],
                                 "mapping": mapping,
                                 "human": not role and origin in (None, "user")
                                          and "subagent" not in str(source).lower()}
        result = {}
        for key in keys:
            record = records.get(key)
            candidates = parents.get(key, set())
            parent = records.get(next(iter(candidates))) if len(candidates) == 1 else None
            if (parent and parent["human"] and parent["chat_id"] not in children
                    and (not record or record["mapping"] in ("UNASSIGNED", "EXACT_ROOT", "AMBIGUOUS_ROOT")
                         or (record["project_id"] and record["project_id"] == parent["project_id"]))):
                result[key] = parent
            elif record:
                result[key] = record
        return result
