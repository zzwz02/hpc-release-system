"""Startup catalogs must preserve default models and separate provider pickers."""
import copy
import runpy
from pathlib import Path

import pytest

merge = runpy.run_path(str(Path(__file__).parents[1] / "deploy/jira-agent/codex/merge-model-catalogs.py"))["merge_catalogs"]


def test_merge_preserves_metadata_and_hides_only_custom_models():
    base = {"identity": "private cache metadata", "models": [
        {"slug": "default", "visibility": "list", "context_window": 500000},
        {"slug": "internal", "visibility": "hide"}]}
    custom = {"models": [{"slug": "GLM-5.3", "visibility": "list",
                          "context_window": 32768, "shell_type": "shell_command"}]}
    original = copy.deepcopy(custom)
    result = merge(base, [custom])
    assert result["models"][:2] == base["models"]
    assert result["models"][2] == {**custom["models"][0], "visibility": "hide"}
    assert list(result) == ["models"]
    assert custom == original


@pytest.mark.parametrize("extra", [{"models": []}, {"models": [{"slug": "default"}]},
                                   {"models": [{}]}])
def test_merge_rejects_ambiguous_catalogs(extra):
    with pytest.raises(ValueError):
        merge({"models": [{"slug": "default"}]}, [extra])
