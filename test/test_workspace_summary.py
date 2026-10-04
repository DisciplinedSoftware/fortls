from pathlib import Path

import pytest
from setup_tests import run_request, write_rpc_notification, write_rpc_request

from fortls.interface import cli
from fortls.langserver import LangServer

# The in-process tests initialise the workspace with a pool of processes
pytestmark = pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded:DeprecationWarning"
)

MODULE = """module shapes
  implicit none
  integer :: n_shapes = 0
  type :: circle
    real :: radius
  end type circle
contains
  function area(c, scale) result(a)
    type(circle), intent(in) :: c
    real, intent(in) :: scale
    real :: a
    real :: tmp
    tmp = 3.14 * c%radius**2
    if (tmp > 0) then
      a = scale * tmp
    end if
  end function area
  subroutine count_shape()
    integer :: i
    do i = 1, 2
      n_shapes = n_shapes + 1
    end do
  end subroutine count_shape
end module shapes
"""

PROGRAM = """program main
  use shapes, only: circle, area, n_shapes
  implicit none
  type(circle) :: c
  real :: x
  x = area(c, 2.0)
  n_shapes = 1
end program main
"""


class Conn:
    def send_notification(self, *args, **kwargs):
        pass


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "shapes.f90").write_text(MODULE)
    (tmp_path / "main.f90").write_text(PROGRAM)
    return tmp_path


def init_server(root: Path) -> LangServer:
    server = LangServer(Conn(), vars(cli("fortls").parse_args(["-n", "1"])))
    server.serve_initialize({"params": {"rootPath": str(root)}})
    return server


def names(scope) -> set[str]:
    return {child.name.lower() for child in scope.children}


def test_workspace_files_are_summarized(project):
    server = init_server(project)
    shapes = server.workspace[str(project / "shapes.f90")]
    assert shapes.summary
    assert shapes.contents_split == []
    module = shapes.ast.global_dict["shapes"]
    assert names(module) == {"n_shapes", "circle", "area", "count_shape"}
    area = next(c for c in module.children if c.name == "area")
    # The signature is kept: arguments and result, not the local variable
    assert names(area) == {"c", "scale", "a"}
    count_shape = next(c for c in module.children if c.name == "count_shape")
    # Neither the local variable nor the DO block
    assert names(count_shape) == set()
    assert names(module.children[1]) == {"radius"}
    assert [scope.name for scope in shapes.ast.scope_list] == [
        "shapes",
        "circle",
        "area",
        "count_shape",
    ]
    # Links to the kept objects are resolved
    assert [arg.name for arg in area.arg_objs] == ["c", "scale"]
    assert area.result_obj.name == "a"


def test_open_parses_in_full_and_close_summarizes(project):
    server = init_server(project)
    path = str(project / "shapes.f90")
    request = {"params": {"textDocument": {"uri": path}}}
    server.serve_onOpen(request)
    shapes = server.workspace[path]
    assert not shapes.summary
    assert shapes.contents_split[0] == "module shapes"
    count_shape = shapes.ast.global_dict["shapes"].children[3]
    assert names(count_shape) == {"i", "#do1"}
    server.serve_onClose(request)
    assert shapes.summary
    assert shapes.contents_split == []
    assert names(shapes.ast.global_dict["shapes"].children[3]) == set()


def test_request_parses_summarized_file_in_full(project):
    server = init_server(project)
    path = str(project / "shapes.f90")
    hover = server.serve_hover(
        {
            "params": {
                "textDocument": {"uri": path},
                "position": {"line": 11, "character": 13},
            }
        }
    )
    assert "REAL :: tmp" in hover["contents"]["value"]
    assert not server.workspace[path].summary


def test_definition_and_references_in_summarized_files(project):
    shapes, main = project / "shapes.f90", project / "main.f90"
    request_string = write_rpc_request(1, "initialize", {"rootPath": str(project)})
    # shapes.f90 is summarized: the position is found in the file on disk
    request_string += write_rpc_request(
        2,
        "textDocument/definition",
        {"textDocument": {"uri": str(main)}, "position": {"line": 5, "character": 7}},
    )
    request_string += write_rpc_notification(
        "textDocument/didClose", {"textDocument": {"uri": str(main)}}
    )
    request_string += write_rpc_notification(
        "textDocument/didOpen", {"textDocument": {"uri": str(shapes)}}
    )
    # main.f90 is summarized again: its references are found in a full parse
    request_string += write_rpc_request(
        3,
        "textDocument/references",
        {
            "textDocument": {"uri": str(shapes)},
            "position": {"line": 2, "character": 14},
        },
    )
    errcode, results = run_request(request_string, ["-n 2", "--disable_diagnostics"])
    assert errcode == 0
    assert Path(results[1]["uri"]).name == "shapes.f90"
    assert results[1]["range"]["start"] == {"line": 7, "character": 11}
    refs = sorted(
        (Path(ref["uri"]).name, ref["range"]["start"]["line"]) for ref in results[2]
    )
    assert refs == [
        ("main.f90", 1),
        ("main.f90", 6),
        ("shapes.f90", 2),
        ("shapes.f90", 20),
        ("shapes.f90", 20),
    ]
