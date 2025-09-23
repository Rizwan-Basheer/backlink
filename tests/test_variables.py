from __future__ import annotations

from backlink.services.variables import VariablesManager


def test_substitute_placeholders_with_env(monkeypatch):
    monkeypatch.setenv("WP_USER", "alice")
    text = "Login as {{env.WP_USER}}"
    result = VariablesManager.substitute_placeholders(text, {})
    assert result == "Login as alice"


def test_substitute_placeholders_nested_mapping():
    variables = {"datasets": {"accounts": {"username": "bob"}}}
    text = "User={{datasets.accounts.username}}"
    result = VariablesManager.substitute_placeholders(text, variables)
    assert result == "User=bob"
