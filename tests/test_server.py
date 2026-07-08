# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for the MCP service logic and the JSON-RPC stdio transport."""

import datetime
import io
import json
import subprocess
from pathlib import Path

import pytest

from canonia import access
from canonia.graph import Graph
from canonia.schema import Concept
from canonia.server import CanonService, StdioServer, ToolError, resolve_identity


def _canon(tmp_path: Path, *, canon_name: str = None, autocommit: bool = False) -> Path:
    """Scaffold a canon. autocommit defaults to False so non-git tests stay
    quiet; pass autocommit=None to omit the git: block (the real default)."""
    canon_block = "canon:\n  root: concepts\n  domains: [process, infra]\n"
    if canon_name:
        canon_block += f"  name: {canon_name}\n"
    yml = canon_block + 'schema:\n  id_pattern: "^[a-z0-9][a-z0-9-]*$"\n'
    if autocommit is not None:
        yml += f"git:\n  autocommit: {str(autocommit).lower()}\n"
    (tmp_path / "canonia.yml").write_text(yml, encoding="utf-8")
    proc = tmp_path / "concepts" / "process"
    proc.mkdir(parents=True)
    c = Concept(id="testing", title="Testing", domain="process",
                summary="Test behavior not implementation.",
                source=[{"repo": "r", "path": "testing.md"}],
                references=[], body="Cover the unhappy paths.")
    (proc / "testing.md").write_text(c.to_markdown(), encoding="utf-8")
    (tmp_path / "concepts" / "infra").mkdir(parents=True)
    return tmp_path


def test_service_create_get_search_update_roundtrip(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))

    created = svc.create(id="ci", title="Continuous integration", domain="process",
                         summary="Integrate small changes often.", references=["testing"])
    assert created["ok"] and created["created"] and created["warnings"] == []
    assert (tmp_path / "concepts" / "process" / "ci.md").exists()

    got = svc.get("ci")
    assert got["title"] == "Continuous integration"
    assert got["references"] == ["testing"]

    # backlink shows up on the referenced concept
    assert "ci" in svc.get("testing")["referenced_by"]

    hits = svc.search("integrate changes")["results"]
    assert hits and hits[0]["id"] == "ci"

    svc.update("ci", append_body="CI runs on every push.")
    assert "every push" in svc.get("ci")["body"]

    assert svc.list_domains()["domains"]["process"] == 2


def test_service_create_rejects_duplicate_and_bad_domain(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    with pytest.raises(ToolError):
        svc.create(id="testing", title="Dup", domain="process", summary="x")
    with pytest.raises(ToolError):
        svc.create(id="x", title="X", domain="nonsense", summary="x")


def test_service_create_rejects_duplicate_id_in_another_domain(tmp_path: Path):
    # Ids are globally unique: an id living under process/ must not be
    # creatable under infra/ (the impostor would shadow the original on load).
    svc = CanonService(_canon(tmp_path))
    with pytest.raises(ToolError, match="already exists"):
        svc.create(id="testing", title="Imposter", domain="infra", summary="x")
    assert not (tmp_path / "concepts" / "infra" / "testing.md").exists()


def test_write_path_containment_survives_loose_id_pattern(tmp_path: Path):
    # Containment must hold even when canonia.yml loosens the id pattern —
    # the regex is validation, not the filesystem boundary.
    (tmp_path / "canonia.yml").write_text(
        "canon:\n  root: concepts\n  domains: [process]\n"
        'schema:\n  id_pattern: "^[a-z0-9./-]+$"\n',
        encoding="utf-8",
    )
    (tmp_path / "concepts" / "process").mkdir(parents=True)
    svc = CanonService(tmp_path)
    with pytest.raises(ToolError, match="outside the canon"):
        svc.create(id="../../escaped", title="X", domain="process", summary="s")
    assert not (tmp_path / "escaped.md").exists()


def test_writes_leave_no_temp_files(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    svc.create(id="a", title="A", domain="process", summary="a")
    svc.update("a", summary="changed")
    assert list(tmp_path.rglob("*.tmp")) == []


def test_service_create_warns_on_unresolved_reference(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    res = svc.create(id="a", title="A", domain="process", summary="a", references=["ghost"])
    assert res["ok"]
    assert any("ghost" in w for w in res["warnings"])


def test_create_uses_configurable_canon_name_for_provenance(tmp_path: Path):
    svc = CanonService(_canon(tmp_path, canon_name="my-canon"))
    svc.create(id="a", title="A", domain="process", summary="a")
    assert svc.get("a")["source"] == [{"repo": "my-canon", "path": "concepts/process/a.md"}]


def test_create_provenance_defaults_to_canon(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    svc.create(id="a", title="A", domain="process", summary="a")
    assert svc.get("a")["source"][0]["repo"] == "canon"


def test_autocommit_commits_each_write(tmp_path: Path):
    _canon(tmp_path, autocommit=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)

    svc = CanonService(tmp_path)
    res = svc.create(id="ci", title="CI", domain="process", summary="c")
    assert res["committed"] is True

    log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path,
                         capture_output=True, text=True).stdout
    assert "Create concept 'ci'" in log

    svc.update("ci", summary="changed")
    log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path,
                         capture_output=True, text=True).stdout
    assert "Update concept 'ci'" in log


def test_autocommit_on_by_default_with_git_repo(tmp_path: Path):
    _canon(tmp_path, autocommit=None)  # no git: block in canonia.yml
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    svc = CanonService(tmp_path)
    res = svc.create(id="a", title="A", domain="process", summary="a")
    assert res["committed"] is True


def test_autocommit_default_warns_without_git_repo(tmp_path: Path):
    svc = CanonService(_canon(tmp_path, autocommit=None))  # default on, no repo
    res = svc.create(id="a", title="A", domain="process", summary="a")
    assert res["committed"] is False
    assert any("not a git repository" in w for w in res["warnings"])


def test_autocommit_records_identity_as_git_author(tmp_path: Path):
    _canon(tmp_path, autocommit=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    svc = CanonService(tmp_path, identity=access.Identity("bot-1", "llm"))
    res = svc.create(id="k", title="K", domain="process", summary="k", status="active")
    assert res["committed"] is True
    author = subprocess.run(["git", "log", "-1", "--format=%an <%ae>"], cwd=tmp_path,
                            capture_output=True, text=True).stdout.strip()
    assert author == "bot-1 <llm@canonia>"


# --- trust layer: identity, draft-by-default, versions, timestamps -----------

def test_llm_identity_creates_land_as_draft(tmp_path: Path):
    svc = CanonService(_canon(tmp_path), identity=access.Identity("bot", "llm"))
    res = svc.create(id="k", title="K", domain="process", summary="k")
    assert res["status"] == "draft"
    assert svc.get("k")["status"] == "draft"
    # drafts are live: searchable and resolvable
    assert "k" in [r["id"] for r in svc.search("k")["results"]]
    # an explicit status is honored (operator's call, until governance lands)
    res2 = svc.create(id="k2", title="K2", domain="process", summary="k", status="active")
    assert res2["status"] == "active"


def test_human_and_anonymous_creates_default_active(tmp_path: Path):
    svc = CanonService(_canon(tmp_path), identity=access.Identity("andre", "human"))
    assert svc.create(id="h", title="H", domain="process", summary="h")["status"] == "active"
    anon = CanonService(tmp_path)  # v0.1-compatible open behavior
    assert anon.create(id="n", title="N", domain="process", summary="n")["status"] == "active"


def test_update_expected_version_detects_concurrent_edit(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    v1 = svc.create(id="k", title="K", domain="process", summary="k")["version"]
    assert svc.get("k")["version"] == v1

    svc.update("k", summary="edited by someone else")   # concurrent edit
    with pytest.raises(ToolError, match="changed since"):
        svc.update("k", summary="mine", expected_version=v1)

    v2 = svc.get("k")["version"]
    res = svc.update("k", summary="mine", expected_version=v2)
    assert res["ok"] and res["version"] != v2
    assert svc.get("k")["summary"] == "mine"


def test_server_writes_stamp_created_and_updated(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    svc.create(id="k", title="K", domain="process", summary="k")
    today = datetime.date.today().isoformat()
    got = svc.get("k")
    assert got["created"] == today and got["updated"] == today
    raw = (tmp_path / "concepts" / "process" / "k.md").read_text(encoding="utf-8")
    assert f"created: {today}" in raw and f"updated: {today}" in raw


def test_resolve_identity_flags_env_and_default_kind(monkeypatch):
    monkeypatch.delenv("CANONIA_IDENTITY", raising=False)
    monkeypatch.delenv("CANONIA_IDENTITY_KIND", raising=False)
    assert resolve_identity() is access.ANONYMOUS
    assert resolve_identity("bot-1").kind == "llm"        # named ⇒ llm unless said
    assert resolve_identity("a", "human").kind == "human"
    monkeypatch.setenv("CANONIA_IDENTITY", "env-bot")
    assert resolve_identity().name == "env-bot"
    monkeypatch.setenv("CANONIA_IDENTITY_KIND", "human")
    assert resolve_identity().kind == "human"
    with pytest.raises(ValueError):
        resolve_identity("x", "robot")


def test_update_domain_change_relocates_file(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    svc.create(id="mover", title="Mover", domain="process", summary="m")
    svc.update("mover", domain="infra")
    assert not (tmp_path / "concepts" / "process" / "mover.md").exists()
    assert (tmp_path / "concepts" / "infra" / "mover.md").exists()


# --- lifecycle primitives ---------------------------------------------------

def _cluster(tmp_path: Path) -> CanonService:
    """A canon with a -> b (a references b), plus a standalone c."""
    svc = CanonService(_canon(tmp_path))
    svc.create(id="b", title="B", domain="process", summary="b")
    svc.create(id="a", title="A", domain="process", summary="a", references=["b"])
    svc.create(id="c", title="C", domain="process", summary="c")
    return svc


def test_deprecate_keeps_concept_resolvable(tmp_path: Path):
    svc = _cluster(tmp_path)
    res = svc.deprecate("b", superseded_by="c", reason="folded conventions")
    assert res["status"] == "deprecated"
    got = svc.get("b")
    assert got["status"] == "deprecated" and got["superseded_by"] == "c"
    # a still references b, and the gate is still clean
    assert svc.get("a")["references"] == ["b"]
    assert Graph.load(svc.config.concepts_dir).validate(domains=("process", "infra")) == []


def test_merge_creates_redirect_and_get_follows(tmp_path: Path):
    svc = _cluster(tmp_path)
    # add a second concept d that we merge into b
    svc.create(id="d", title="D", domain="process", summary="d dup", references=["c"])
    res = svc.merge("d", into="b")
    assert res["merged_into"] == "b" and res["repointed"] == []

    # raw tombstone
    raw = svc.get("d", follow=False)
    assert raw["status"] == "merged" and raw["redirect"] == "b"
    # get follows the redirect transparently
    followed = svc.get("d")
    assert followed["id"] == "b" and followed["redirected_from"] == "d"
    # b absorbed d's provenance
    assert any(s["path"].endswith("d.md") for s in svc.get("b")["source"])
    # gate stays clean (redirect target exists)
    assert Graph.load(svc.config.concepts_dir).validate(domains=("process", "infra")) == []


def test_merge_repoint_rewrites_inbound_references(tmp_path: Path):
    svc = _cluster(tmp_path)  # a -> b
    res = svc.merge("b", into="c", repoint=True)
    assert res["repointed"] == ["a"]
    assert svc.get("a")["references"] == ["c"]  # a now points at c directly


def test_merge_is_transactional_on_validation_failure(tmp_path: Path):
    # A merge that fails validation must write NOTHING: previously the target
    # was saved (absorbing the source's provenance) before the source's
    # validation error fired, leaving a half-merged canon with no tombstone.
    svc = _cluster(tmp_path)
    legacy = Concept(id="legacy", title="Legacy", domain="process", summary="",
                     source=[{"repo": "old", "path": "legacy.md"}], body="Old notes.")
    (tmp_path / "concepts" / "process" / "legacy.md").write_text(
        legacy.to_markdown(), encoding="utf-8"
    )
    target_path = tmp_path / "concepts" / "process" / "c.md"
    before = target_path.read_text(encoding="utf-8")

    with pytest.raises(ToolError):
        svc.merge("legacy", into="c")   # legacy has no summary -> invalid

    assert target_path.read_text(encoding="utf-8") == before  # no absorbed provenance
    assert svc.get("legacy", follow=False)["status"] != "merged"  # no tombstone


def test_merge_rejects_target_that_is_a_redirect(tmp_path: Path):
    svc = _cluster(tmp_path)
    svc.merge("b", into="c")  # b -> c
    with pytest.raises(ToolError):
        svc.merge("a", into="b")  # b is now a redirect


def test_archive_hides_from_search_but_keeps_resolvable(tmp_path: Path):
    svc = _cluster(tmp_path)
    svc.archive("c")
    ids = [r["id"] for r in svc.search("c")["results"]]
    assert "c" not in ids
    assert "c" in [r["id"] for r in svc.search("c", include_archived=True)["results"]]
    assert svc.get("c")["status"] == "archived"  # still resolvable
    assert svc.list_domains()["archived"] == 1


def test_restore_brings_back_archived(tmp_path: Path):
    svc = _cluster(tmp_path)
    svc.archive("c")
    svc.restore("c")
    assert svc.get("c")["status"] == "active"


def test_remove_gated_on_zero_dependents(tmp_path: Path):
    svc = _cluster(tmp_path)  # a -> b
    with pytest.raises(ToolError) as exc:
        svc.remove("b")  # a depends on b
    assert "depend" in str(exc.value)
    # c has no dependents -> removable
    res = svc.remove("c")
    assert res["ok"] and res["dependents_broken"] == []
    with pytest.raises(ToolError):
        svc.get("c")


def test_remove_gated_on_inline_body_reference(tmp_path: Path):
    # dependents() must count inline [[id]] refs, exactly like the validate
    # gate does — otherwise remove can leave the canon failing its own gate.
    svc = _cluster(tmp_path)
    svc.update("a", body="See [[c]] for details.")
    with pytest.raises(ToolError) as exc:
        svc.remove("c")
    assert "depend" in str(exc.value)


def test_remove_force_breaks_dependents(tmp_path: Path):
    svc = _cluster(tmp_path)  # a -> b
    res = svc.remove("b", force=True)
    assert res["dependents_broken"] == ["a"]
    # the canon now has a genuine dangling reference (a -> b), caught by the gate
    issues = Graph.load(svc.config.concepts_dir).validate(domains=("process", "infra"))
    assert any("dangling reference" in i.message and i.concept == "a" for i in issues)


def test_broken_redirect_is_caught_by_gate(tmp_path: Path):
    svc = _cluster(tmp_path)
    svc.merge("a", into="b")  # a -> redirect b
    # force-remove the redirect target; a's redirect now dangles
    svc.remove("b", force=True)
    issues = Graph.load(svc.config.concepts_dir).validate(domains=("process", "infra"))
    assert any(i.field == "redirect" for i in issues)


# --- JSON-RPC transport -----------------------------------------------------

def _rpc(server: StdioServer, out: io.StringIO):
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_stdio_initialize_list_and_call(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "get", "arguments": {"id": "testing"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "get", "arguments": {"id": "missing"}}}),
    ]) + "\n"
    out = io.StringIO()
    StdioServer(svc, stdin=io.StringIO(requests), stdout=out, stderr=io.StringIO()).run()
    responses = _rpc(StdioServer(svc), out)

    by_id = {r.get("id"): r for r in responses}
    # initialize
    assert by_id[1]["result"]["serverInfo"]["name"] == "canonia"
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    # notification produced no response -> only ids 1..4 present
    assert set(by_id) == {1, 2, 3, 4}
    # tools/list
    names = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert {"search", "get", "create", "update", "list_domains",
            "deprecate", "merge", "archive", "restore", "remove"} == names
    # tools/call get -> structured content
    assert by_id[3]["result"]["structuredContent"]["id"] == "testing"
    assert by_id[3]["result"]["isError"] is False
    # tools/call get missing -> tool error result (not a protocol error)
    assert by_id[4]["result"]["isError"] is True


def test_stdio_unknown_method_is_protocol_error(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    req = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "bogus"}) + "\n"
    out = io.StringIO()
    StdioServer(svc, stdin=io.StringIO(req), stdout=out, stderr=io.StringIO()).run()
    resp = json.loads(out.getvalue().strip())
    assert resp["error"]["code"] == -32601


# --- low-audit regressions: merge edges, search inputs, arg types ------------

def test_merge_rejects_archived_target(tmp_path: Path):
    # Tombstones and archived concepts are both excluded from search, so a
    # merge into an archived target would make the source's content vanish.
    svc = _cluster(tmp_path)
    svc.archive("c")
    with pytest.raises(ToolError, match="archived"):
        svc.merge("b", into="c")
    assert svc.get("b", follow=False)["status"] != "merged"  # nothing written


def test_merge_rejects_remerging_a_tombstone(tmp_path: Path):
    svc = _cluster(tmp_path)
    svc.merge("b", into="c")
    with pytest.raises(ToolError, match="already merged"):
        svc.merge("b", into="a")  # would silently re-target b's redirect
    assert svc.get("b", follow=False)["redirect"] == "c"


def test_search_empty_and_punctuation_queries_return_nothing(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    for query in ("", "???", "..."):
        out = svc.search(query)
        assert out["count"] == 0 and out["results"] == []


def test_search_rejects_nonpositive_limit(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    with pytest.raises(ToolError, match="limit"):
        svc.search("testing", limit=0)


def test_stdio_rejects_wrongly_typed_tool_args(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "update",
                               "arguments": {"id": "testing", "references": "abc"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "search",
                               "arguments": {"query": "x", "limit": "5"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "get", "arguments": {"bogus": True}}}),
    ]) + "\n"
    out = io.StringIO()
    StdioServer(svc, stdin=io.StringIO(requests), stdout=out, stderr=io.StringIO()).run()
    by_id = {r.get("id"): r for r in _rpc(StdioServer(svc), out)}

    # references="abc" must NOT be accepted and iterated into ['a', 'b', 'c'].
    assert by_id[1]["result"]["isError"] is True
    assert "array" in by_id[1]["result"]["content"][0]["text"]
    assert svc.get("testing")["references"] == []
    assert by_id[2]["result"]["isError"] is True   # limit as a string
    assert by_id[3]["result"]["isError"] is True   # unknown arg / missing id


def test_stdio_initialize_negotiates_protocol_version(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "1999-01-01"}}) + "\n"
    out = io.StringIO()
    StdioServer(svc, stdin=io.StringIO(req), stdout=out, stderr=io.StringIO()).run()
    resp = json.loads(out.getvalue().strip())
    # Not an echo: the server answers with the revision it implements.
    assert resp["result"]["protocolVersion"] == "2025-06-18"


def test_tool_results_report_canon_relative_paths(tmp_path: Path):
    # Absolute host paths (user name, machine layout) must not leak to clients.
    svc = CanonService(_canon(tmp_path))
    created = svc.create(id="rel", title="Rel", domain="process", summary="s")
    assert created["path"] == "concepts/process/rel.md"

    moved = svc.update("rel", domain="infra")
    assert moved["path"] == "concepts/infra/rel.md"
    assert moved["moved_from"] == "concepts/process/rel.md"

    removed = svc.remove("rel")
    assert removed["removed"] == "concepts/infra/rel.md"


def test_stdio_oversized_message_rejected_without_killing_server(tmp_path: Path):
    svc = CanonService(_canon(tmp_path))
    big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "pad": "x" * 500})
    ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    out = io.StringIO()
    srv = StdioServer(svc, stdin=io.StringIO(big + "\n" + ping + "\n"),
                      stdout=out, stderr=io.StringIO())
    srv.MAX_MESSAGE_CHARS = 100          # shrink the cap for the test
    srv.run()
    responses = _rpc(StdioServer(svc), out)

    assert responses[0]["error"]["code"] == -32600
    assert "size" in responses[0]["error"]["message"]
    assert responses[1]["result"] == {}  # the next message still got served
