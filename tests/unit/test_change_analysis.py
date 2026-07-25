from atomic_commits.change_analysis import build_change_graph
from atomic_commits.git_client import GitClient
from atomic_commits.models import FileChange, Hunk, WorktreeSnapshot
from atomic_commits.scanner import scan
from tests.integration.helpers import git, make_cfg


def test_change_graph_records_python_symbol_and_test_link(git_repo):
    (git_repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (git_repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    git(git_repo, "add", "calc.py", "test_calc.py")
    git(git_repo, "commit", "-q", "-m", "seed calculator")
    (git_repo / "calc.py").write_text("def add(a, b):\n    return int(a) + int(b)\n")
    (git_repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(\"1\", 2) == 3\n"
    )

    snapshot = scan(GitClient(git_repo), make_cfg(git_repo))
    graph = build_change_graph(snapshot)

    assert any(unit.symbol == "add" for unit in graph.units)
    assert any(link.kind == "test" for link in graph.links)


def test_change_graph_uses_one_import_bridge_instead_of_every_hunk_pair(tmp_path):
    (tmp_path / "a.py").write_text("import b\n")
    (tmp_path / "b.py").write_text("")

    def changed_file(path: str) -> FileChange:
        return FileChange(
            path=path,
            status="modified",
            hunks=[
                Hunk(
                    hunk_id=f"{path}-{index}", file_path=path, old_start=index,
                    old_count=1, new_start=index, new_count=1, header="", patch="",
                )
                for index in range(10)
            ],
        )

    graph = build_change_graph(WorktreeSnapshot(
        repo_root=tmp_path, branch="main", head_sha="abc",
        files=[changed_file("a.py"), changed_file("b.py")],
    ))

    assert len([link for link in graph.links if link.kind == "import"]) == 1
