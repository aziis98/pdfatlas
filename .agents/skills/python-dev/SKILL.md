---
name: python-dev
description: >-
  Coding style conventions, static typing guidelines, and architecture patterns for
  Python development in this repository. Use whenever authoring, refactoring, or
  reviewing Python code, classes, controllers, or UI components.
---

# Python Development & Style Conventions

Guidelines and architectural conventions for writing clean, strongly typed, and non-defensive Python code in this codebase.

---

## 1. Strict Typing & Zero `Any` Policy

- **No `Any` for Internal Types:** Do not use `Any` for parent/window references, controllers, callbacks, or cache models.
- **Typed Hosts & Unions:** When a component is shared across hosts (e.g., `MainWindow` and `PdfDocumentView`), type the host as a concrete union (`MainWindow | PdfDocumentView`) or define a `@runtime_checkable` `Protocol`.
- **Cyclic Imports:** Use `from typing import TYPE_CHECKING` with `from __future__ import annotations` to import types without runtime circular dependencies.
- **Structural Protocols:** Use `typing.Protocol` with runtime checkability for structural geometry/mocks (e.g. `CropRectProtocol`) instead of accepting `Any`.
- **Explicit Callbacks:** Always annotate callbacks with precise signatures (e.g. `Callable[[int, float, float], None] | None` instead of `Callable` or `Any`).

---

## 2. Eliminating Defensive Anti-Patterns

- **No `getattr` / `hasattr` Dynamic Lookups:** Avoid `getattr(self.win, "attr", None)` or `hasattr(...)` for properties that belong to the class contract. Define typed attributes explicitly on the class or use explicit `isinstance(target, TargetClass)` narrowing.
- **Avoid Proliferating `| None` (Pre-instantiate in `__init__`):**
  - Do not default widgets, sub-controllers, or state containers to `None` if they can be initialized in `__init__`.
  - Instantiating widgets directly in `__init__` eliminates cascading `if obj is not None:` guards and makes the object lifecycle deterministic.
- **No `try...except ImportError` Fallbacks:** Standard project dependencies (e.g., `fitz`, `gi`, `cairo`, `numpy`, `pydantic`) are guaranteed in the environment. Import them directly at top-level.

---

## 3. Automated Quality Verification

Always verify code edits with the project's quality toolchain:

```bash
uv run pyright
uv run ruff check .
uv run pytest
```

Ensure all commands exit cleanly with **0 errors**.
