"""Generic agent-bridge core.

These modules are deliberately free of any Codex-specific knowledge so they can be
extracted into a standalone `agent-bridge` package once a third bridge appears.
The dependency rule is one-way: `_core` never imports from its parent package.
"""
