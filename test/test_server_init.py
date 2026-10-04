import os

import pytest
from setup_tests import Path, run_request, write_rpc_request

from fortls.constants import Severity


@pytest.fixture()
def setup_tmp_file(tmp_path):
    # In its own directory: the workspace root is the file's parent
    levels = 2000
    filename = tmp_path / "nested_if.f90"
    filename.write_text(
        "program nested_if\n"
        + str("if (.true.) then\n" * levels)
        + str("end if\n" * levels)
        + "end program nested_if"
    )
    return str(filename)


def test_recursion_error_handling(setup_tmp_file):
    root = Path(setup_tmp_file).parent
    request_string = write_rpc_request(1, "initialize", {"rootPath": str(root)})
    errcode, results = run_request(request_string)
    assert errcode == 0
    assert results[0]["type"] == Severity.error


# Before Python 3.13 "**" matches only directories, "*" also matches symlinks
@pytest.mark.parametrize("glob", ["./**", "./*"])
def test_dangling_symlink_in_source_dirs(tmp_path, glob):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "bar.f90").write_text("module bar_mod\nend module bar_mod\n")
    try:
        os.symlink(tmp_path / "nonexistent", tmp_path / "dead_link")
        os.symlink(tmp_path / "loop_link", tmp_path / "loop_link")
    except OSError:
        pytest.skip("Creating symlinks is not supported on this system")
    request_string = write_rpc_request(1, "initialize", {"rootPath": str(tmp_path)})
    request_string += write_rpc_request(2, "workspace/symbol", {"query": "bar_mod"})
    errcode, results = run_request(request_string, [f"--source_dirs {glob}"])
    assert errcode == 0
    assert results[1][0]["name"] == "bar_mod"


@pytest.fixture()
def six_files(tmp_path):
    for d in ("a", "b", "c"):
        (tmp_path / d).mkdir()
        for i in (1, 2):
            (tmp_path / d / f"{d}{i}.f90").write_text(
                f"module {d}{i}_mod\nend module {d}{i}_mod\n"
            )
    return tmp_path


def init_and_find_module(root: Path, fortls_args: list[str]) -> list:
    request_string = write_rpc_request(1, "initialize", {"rootPath": str(root)})
    request_string += write_rpc_request(2, "workspace/symbol", {"query": "a1_mod"})
    errcode, results = run_request(request_string, fortls_args)
    assert errcode == 0
    return results


def test_max_workspace_files_exceeded(six_files):
    results = init_and_find_module(six_files, ["--max_workspace_files 5"])
    assert results[0]["type"] == Severity.warn
    assert results[0]["message"].startswith(
        "Workspace not indexed: more than 5 Fortran source files under"
    )
    assert results[2] == []


@pytest.mark.parametrize(
    "fortls_args",
    [
        ["--max_workspace_files 6"],
        ["--max_workspace_files 0"],
        ["--max_workspace_files 1", "--source_dirs ./**"],
        ["--max_workspace_files 5", "--excl_suffixes 2.f90"],
    ],
)
def test_max_workspace_files_not_exceeded(six_files, fortls_args):
    results = init_and_find_module(six_files, fortls_args)
    assert results[1][0]["name"] == "a1_mod"
