---
name: python-dev
description: >-
  Coding style conventions, static typing guidelines, and architecture patterns for
  Python development. Use whenever authoring, refactoring, or reviewing Python code,
  classes, controllers, or modules.
---

# Python Development & Style Conventions

Core guidelines and architectural conventions for writing clean, strongly typed, and non-defensive Python code.

---

## 1. Strict Typing & Zero `Any` Policy

- **No `Any` for Internal Types:** Avoid `Any` for object references, data models, state containers, or callbacks.
- **Typed Contexts & Unions:** When a component is shared across multiple contexts or parent classes, type the reference as a concrete `Union` (e.g., `ContextA | ContextB`) or define a `@runtime_checkable` `Protocol`.
- **Cyclic Imports:** Use `from typing import TYPE_CHECKING` with `from __future__ import annotations` to import types without causing runtime circular dependencies.
- **Structural Protocols:** Use `typing.Protocol` with `@runtime_checkable` for structural subtyping, duck typing, and test mocks instead of accepting `Any`.
- **Explicit Callbacks:** Always annotate callables with precise parameter and return signatures (e.g., `Callable[[int, str], None] | None` instead of bare `Callable` or `Any`).

---

## 2. Eliminating Defensive Anti-Patterns

- **No `getattr` / `hasattr` Dynamic Lookups:** Avoid `getattr(obj, "attr", None)` or `hasattr(...)` for properties that belong to the expected class interface. Define typed attributes explicitly on the class or use `isinstance(target, TargetClass)` narrowing.
- **Avoid Proliferating `| None` (Pre-instantiate in `__init__`):**
  - Do not default dependent objects, sub-components, or collections to `None` if they can be created or bound during initialization.
  - Instantiating objects directly in `__init__` eliminates cascading `if obj is not None:` guards and ensures a deterministic object lifecycle.
- **No `try...except ImportError` Fallbacks:** Declared package dependencies are guaranteed in the target environment. Import them directly at top-level rather than wrapping in fallback exception ladders.

---

## 3. Automated Quality Verification

Always verify code edits with static analysis and automated test suites:

- **Static Type Checking:** Verify 0 type errors with type checkers (e.g., Pyright / Mypy).
- **Linter & Formatting:** Ensure clean passes with code linters (e.g., Ruff / Flake8).
- **Unit Tests:** Run targeted and full test suites to prevent regression.
