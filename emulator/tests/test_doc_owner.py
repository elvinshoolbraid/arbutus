"""One owner for the open project: no private reach-ins (#1017 step 2).

`project_file.py` (the named project) made 14 reach-ins into other objects'
private state, and `persist.py` imported it inside functions to dodge an
import cycle. Since #1017 the document half (capture, apply, autosave, the
working state's pointer) is `project_file`'s, `Document` holds what it
remembers, and `persist.ProjectStore` keeps the tape's folder. These tests
read the two files' code (comments and docstrings aside, through `ast`) and
hold that line: neither touches a `_private` name on anything but `self`,
and `persist` does not import `project_file` at all.

The negative control (#56) is a reach-in put back: `store._last_refusal` in
`project_file.py` fails the first test.
"""

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

FILES = ("project_file.py", "persist.py")


def tree(name: str) -> ast.AST:
    with open(os.path.join(HERE, "app", name)) as f:
        return ast.parse(f.read(), name)


def reach_ins(name: str):
    """Every `x._private` in the file's code whose `x` is not `self`."""
    out = []
    for node in ast.walk(tree(name)):
        if not isinstance(node, ast.Attribute):
            continue
        attr = node.attr
        if not attr.startswith("_") or attr.startswith("__"):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            continue
        out.append("%s:%d %s" % (name, node.lineno, ast.unparse(node)))
    return out


class NoReachIns(unittest.TestCase):

    def test_neither_file_touches_another_objects_private_state(self):
        for name in FILES:
            self.assertEqual(reach_ins(name), [], name)

    def test_persist_does_not_import_project_file(self):
        found = []
        for node in ast.walk(tree("persist.py")):
            if isinstance(node, ast.ImportFrom) and (
                    node.module == "project_file"
                    or any(a.name == "project_file" for a in node.names)):
                found.append(node.lineno)
        self.assertEqual(found, [])


class OneOwner(unittest.TestCase):
    """The document's state lives on `Document`, not on `ProjectStore`."""

    def test_the_store_keeps_no_document_state(self):
        from app.persist import ProjectStore
        for name in ("snapshot", "save_project", "load_project", "tick",
                     "flush", "commit", "doc_written", "fresh_key"):
            self.assertFalse(hasattr(ProjectStore, name), name)


if __name__ == "__main__":
    unittest.main()
