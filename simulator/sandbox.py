"""
Restricted execution for user-submitted PreemptionPolicy source.

This is NOT kernel-grade sandboxing. Docker Desktop is not installed and
WSL is not running on this development machine (checked in Phase 1), so
real OS-level isolation (namespaces, cgroups, seccomp) isn't available
here. For a research prototype, this module instead provides two
independent, cheap layers of defense, meant to be combined with running
the actual simulation in a separate OS process with a wall-clock timeout
(see sandbox_runner.py and backend/main.py):

  1. An AST whitelist (`validate_policy_source`) that rejects imports,
     dunder attribute/name access (the standard route to sandbox escapes
     like `().__class__.__bases__`), and any function call whose target
     isn't either a documented-safe builtin or a method/attribute access
     (self.*, core.*, env.*, etc).
  2. A restricted `exec()` builtins dict (`SAFE_BUILTINS`, used by
     sandbox_runner.py) with no `import`, `open`, `exec`, `eval`, or
     `__import__` reachable at runtime, as defense in depth in case
     something slips past the AST pass.

Real hardening (a container, gVisor, nsjail, or a genuine seccomp
profile) is out of scope for this prototype and is called out explicitly
as a known limitation in the docs (Section 24) rather than implied by
this module's name.
"""

from __future__ import annotations
import ast
import builtins as _builtins_module
from typing import Optional


class PolicyValidationError(Exception):
    """Raised when submitted source fails the AST whitelist or doesn't
    define exactly one PreemptionPolicy subclass. Carries a line number
    where available so the API can point the user at the offending line."""

    def __init__(self, message: str, line: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.line = line


# Node types a policy body is allowed to contain. Deliberately excludes:
# Import/ImportFrom, With, Delete, Global, Nonlocal, Yield/YieldFrom,
# Async*, Lambda (not needed for should_defer's shape).
_ALLOWED_NODE_TYPES = (
    ast.Module, ast.ClassDef, ast.FunctionDef,
    ast.arguments, ast.arg,
    ast.Return, ast.Pass, ast.Assert, ast.Raise,
    ast.If, ast.For, ast.While, ast.Break, ast.Continue,
    ast.Try,
    getattr(ast, "TryStar", ast.Try),
    ast.ExceptHandler,
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr,
    ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.And, ast.Or, ast.Not, ast.Invert, ast.UAdd, ast.USub,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
    ast.In, ast.NotIn,
    ast.Call, ast.keyword,
    ast.Name, ast.Load, ast.Store, ast.Del,
    ast.Attribute, ast.Subscript, ast.Slice, ast.Index if hasattr(ast, "Index") else ast.Slice,
    ast.Constant,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.IfExp,
    ast.comprehension, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)

_FORBIDDEN_NODE_MESSAGE = {
    ast.Import: "imports are not allowed inside a policy",
    ast.ImportFrom: "imports are not allowed inside a policy",
    ast.With: "'with' statements are not allowed inside a policy",
    ast.Delete: "'del' is not allowed inside a policy",
    ast.Global: "'global' is not allowed inside a policy",
    ast.Nonlocal: "'nonlocal' is not allowed inside a policy",
    ast.Lambda: "lambdas are not allowed inside a policy — define a method instead",
    ast.Yield: "generators are not allowed inside a policy",
    ast.YieldFrom: "generators are not allowed inside a policy",
}

# Bare function names (not attribute/method calls) a policy may call directly.
_SAFE_CALL_NAMES = {
    "len", "min", "max", "abs", "round", "sum", "isinstance", "issubclass",
    "float", "int", "str", "bool", "super", "range", "sorted", "enumerate",
    "list", "dict", "set", "tuple", "zip", "all", "any", "print",
}

_REQUIRED_BASE_CLASS = "PreemptionPolicy"
_REQUIRED_METHOD = "should_defer"


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def validate_policy_source(source: str) -> str:
    """Validate submitted policy source and return the name of the single
    PreemptionPolicy subclass it defines. Raises PolicyValidationError
    (never a raw exception) on any problem, with a line number attached
    wherever the AST gives us one."""

    try:
        tree = ast.parse(source, filename="<policy>")
    except SyntaxError as e:
        raise PolicyValidationError(e.msg, line=e.lineno) from e

    policy_class_names: list[str] = []

    for node in ast.walk(tree):
        node_type = type(node)

        for forbidden_type, message in _FORBIDDEN_NODE_MESSAGE.items():
            if isinstance(node, forbidden_type):
                raise PolicyValidationError(message, line=getattr(node, "lineno", None))

        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise PolicyValidationError(
                f"'{node_type.__name__}' is not a supported construct here",
                line=getattr(node, "lineno", None),
            )

        if isinstance(node, ast.Name) and _is_dunder(node.id):
            raise PolicyValidationError(
                f"access to '{node.id}' is not allowed", line=node.lineno,
            )

        if isinstance(node, ast.Attribute) and _is_dunder(node.attr):
            raise PolicyValidationError(
                f"access to '.{node.attr}' is not allowed", line=node.lineno,
            )

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id not in _SAFE_CALL_NAMES:
                # Allow calling the class being defined (rare) or a locally
                # defined helper — anything else bare-named is suspicious
                # (e.g. a shadowed/rebound builtin). Cheap and conservative:
                # only the documented safe builtins may be called by bare name.
                raise PolicyValidationError(
                    f"calling '{func.id}(...)' is not allowed "
                    f"— only {sorted(_SAFE_CALL_NAMES)} may be called directly",
                    line=node.lineno,
                )

        if isinstance(node, ast.ClassDef):
            base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
            if _REQUIRED_BASE_CLASS in base_names:
                policy_class_names.append(node.name)

    if len(policy_class_names) == 0:
        raise PolicyValidationError(
            f"no class inheriting from {_REQUIRED_BASE_CLASS} was found — "
            f"define exactly one, e.g. 'class MyPolicy({_REQUIRED_BASE_CLASS}):'"
        )
    if len(policy_class_names) > 1:
        raise PolicyValidationError(
            f"found {len(policy_class_names)} classes inheriting from "
            f"{_REQUIRED_BASE_CLASS} ({policy_class_names}) — submit exactly one"
        )

    class_name = policy_class_names[0]
    class_node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == class_name
    )
    method_names = {
        n.name for n in class_node.body if isinstance(n, ast.FunctionDef)
    }
    if _REQUIRED_METHOD not in method_names:
        raise PolicyValidationError(
            f"class '{class_name}' must define a '{_REQUIRED_METHOD}(self, core, env)' method"
        )

    return class_name


# Builtins exposed to the exec() namespace when the validated policy source
# is actually run (see sandbox_runner.py). No import machinery, no I/O.
_SAFE_BUILTIN_NAMES = [
    "len", "min", "max", "abs", "round", "sum", "isinstance", "issubclass",
    "float", "int", "str", "bool", "super", "range", "sorted", "enumerate",
    "list", "dict", "set", "tuple", "zip", "all", "any", "print",
    "True", "False", "None", "object", "Exception", "ValueError",
    "TypeError", "AttributeError", "StopIteration", "ZeroDivisionError",
    "IndexError", "KeyError", "ArithmeticError", "RuntimeError",
    # Required by the `class` statement itself (CPython calls this
    # internally to construct any class object) — not user-reachable as
    # a callable name since `__build_class__` is a dunder and the AST
    # pass rejects dunder Name/Attribute access before this ever matters.
    "__build_class__",
    "__name__",
]
SAFE_BUILTINS = {name: getattr(_builtins_module, name) for name in _SAFE_BUILTIN_NAMES}
