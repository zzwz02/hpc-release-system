"""Offline review boundaries and provenance, not model-quality golden answers."""
import copy
import json

import pytest

from tools.jira_comment_regression import bounded_context, generation_prompt, review_prompt, review_schema, validate_review


@pytest.fixture
def source():
    return {
        "case": {"turn_id": "current", "conversation_id": "thread", "seq": 2},
        "current_user_messages": [{"turn_id": "current", "payload": {"text": "继续验证"}}],
        "current_analysis": {"summary": "恢复数据后验证候选，验收通过再审核提交。",
                             "ownership": {"belongs_to_us": "yes"},
                             "evidence": [{"description": "启动通过，完整测试未执行"}],
                             "comment": {"body": "OLD_CURRENT_COMMENT"}},
        "current_materials": [{"turn_id": "current", "content": "原测试需要只读数据目录。"}],
        "prior_history": [{"seq": 1, "conversation_id": "thread", "events": [
            {"kind": "user_message", "payload": {"text": "初始问题"}},
            {"kind": "result", "payload": {"summary": "已发现依赖错误", "comment": "OLD_HISTORY_COMMENT"}},
            {"kind": "jira_comment", "payload": {"text": "PUBLISHED_COMMENT"}},
        ]}],
        "reference_cards": "REFERENCE_ANSWER",
        "future_rounds": ["FUTURE_FINDING"],
        "limitations": ["TEST_ENVIRONMENT_HAS_NO_EXECUTION_MACHINE"],
    }


def draft():
    return {"kind": "fix", "outcome": "候选恢复启动。", "body": "先提交补丁，再测试。",
            "evidence_indices": [0], "commands": ["run-original --strict"], "link_text": "补丁与验证记录"}


def review():
    return {"required_facts": [{"source_path": "/current_analysis/summary",
                               "source_quote": "验收通过再审核提交", "why_needed": "保留验证先于提交的条件。",
                               "comment_quote": "验收通过再审核提交"}],
            "findings": [{"category": "meaning_change", "action": "rewrite", "field": "body",
                          "quote": "先提交补丁，再测试。", "replacement": "恢复数据后验证候选，验收通过再审核提交。",
                          "source_path": "/current_analysis/summary",
                          "source_quote": "验收通过再审核提交", "reason": "将提交提前到了完整验证之前。"}],
            "comment": {**draft(), "body": "恢复数据后验证候选，验收通过再审核提交。"}}


def test_generating_and_reviewing_only_receive_the_bounded_round(source):
    before = copy.deepcopy(source)
    for prompt in [generation_prompt(source, "写作要求"), review_prompt(source, draft(), "写作要求")]:
        assert all(secret not in prompt for secret in
                   ["OLD_CURRENT_COMMENT", "OLD_HISTORY_COMMENT", "PUBLISHED_COMMENT", "REFERENCE_ANSWER",
                    "FUTURE_FINDING", "TEST_ENVIRONMENT_HAS_NO_EXECUTION_MACHINE"])
        assert "已发现依赖错误" in prompt and "恢复数据后验证候选" in prompt
    assert source == before
    assert bounded_context(source)["current_analysis"]["ownership"] == {"belongs_to_us": "yes"}


@pytest.mark.parametrize("seq", [2, 3, True, 0])
def test_reject_current_or_future_history(source, seq):
    source["prior_history"][0]["seq"] = seq
    with pytest.raises(ValueError):
        bounded_context(source)


def test_reject_foreign_history_or_current_material(source):
    source["prior_history"][0]["conversation_id"] = "another"
    with pytest.raises(ValueError):
        bounded_context(source)
    source["prior_history"] = []
    source["current_materials"][0]["turn_id"] = "future"
    with pytest.raises(ValueError):
        bounded_context(source)


def test_review_requires_real_draft_quote_and_real_source_reference(source):
    original = copy.deepcopy(source)
    assert validate_review(review(), source, draft()) == []
    for field, value in [("quote", "不存在的句子"), ("source_quote", "已验证所有测试"),
                         ("source_path", "/future_rounds/0"), ("reason", "")]:
        candidate = review()
        candidate["findings"][0][field] = value
        assert validate_review(candidate, source, draft())
    assert source == original


def test_review_cannot_silently_change_commands_or_output_contract(source):
    candidate = review()
    candidate["comment"]["commands"] = ["run-original --skip-validation"]
    assert "commands changed" in validate_review(candidate, source, draft())
    candidate = review()
    candidate["comment"]["ownership"] = {"belongs_to_us": "no"}
    assert "comment contract changed" in validate_review(candidate, source, draft())
    candidate = review()
    candidate["comment"]["evidence_indices"] = [True]
    assert "invalid evidence reference" in validate_review(candidate, source, draft())


def test_unchanged_review_can_pass_but_unexplained_rewrite_cannot(source):
    correct = review()["comment"]
    assert validate_review({**review(), "findings": [], "comment": correct}, source, correct) == []
    assert "unexplained comment change" in validate_review(
        {**review(), "findings": []}, source, draft())
    assert json.loads(json.dumps(source)) == source


def test_a_declared_edit_cannot_hide_unreported_changes(source):
    candidate = review()
    candidate["comment"]["outcome"] = "全部验证通过。"
    assert "comment does not match declared edits" in validate_review(candidate, source, draft())


@pytest.mark.parametrize("field,value", [
    ("source_path", "/current_analysis"),
    ("source_path", "/future_rounds/0"),
    ("source_quote", "全部验收通过"),
    ("comment_quote", "原文不存在的验证条件"),
    ("why_needed", " "),
])
def test_required_facts_need_exact_source_and_final_text(source, field, value):
    candidate = review()
    candidate["required_facts"][0][field] = value
    assert validate_review(candidate, source, draft())


def test_link_title_cannot_be_used_to_claim_fact_is_in_body(source):
    candidate = review()
    candidate["required_facts"][0]["comment_quote"] = draft()["link_text"]
    assert any("quote absent" in e for e in validate_review(candidate, source, draft()))


@pytest.mark.parametrize("facts", [[], None, {}, ["invalid"]])
def test_vacuous_coverage_cannot_pass(source, facts):
    candidate = review()
    candidate["required_facts"] = facts
    assert validate_review(candidate, source, draft())


def test_merge_delete_and_add_are_exact_and_do_not_mutate_inputs(source):
    original = {**draft(), "body": "正常校验结束。\n\n尚无全量结果。\n\n完整测试未执行。"}
    candidate = review()
    item = candidate["findings"][0]
    candidate["findings"] = [
        {**item, "category": "process_detail", "action": "delete", "quote": "正常校验结束。\n\n", "replacement": ""},
        {**item, "category": "redundancy", "action": "merge", "quote": "尚无全量结果。\n\n完整测试未执行。",
         "replacement": "完整测试未执行。"},
        {**item, "category": "omission", "action": "add", "quote": "",
         "replacement": "\n\n恢复数据后验证候选，验收通过再审核提交。"},
    ]
    candidate["comment"] = {**original, "body": "完整测试未执行。\n\n恢复数据后验证候选，验收通过再审核提交。"}
    before = copy.deepcopy((source, original, candidate))
    assert validate_review(candidate, source, original) == []
    assert (source, original, candidate) == before


def test_edits_are_relative_to_original_and_cannot_overlap(source):
    candidate = review()
    candidate["findings"].append({**candidate["findings"][0], "quote": "先提交补丁", "replacement": "先测试"})
    assert any("overlapping" in e for e in validate_review(candidate, source, draft()))


def test_duplicate_or_wrong_field_quotes_are_rejected(source):
    candidate = review()
    original = {**draft(), "body": draft()["body"] * 2}
    assert any("ambiguous" in e for e in validate_review(candidate, source, original))
    candidate["findings"][0]["field"] = "outcome"
    assert any("absent" in e for e in validate_review(candidate, source, draft()))


@pytest.mark.parametrize("changes", [
    {"action": "add", "quote": "", "category": "process_detail"},
    {"action": "delete", "replacement": "仍有替换内容"},
    {"action": "merge", "replacement": ""},
    {"field": "commands"},
])
def test_invalid_edit_operations_fail_closed(source, changes):
    candidate = review()
    candidate["findings"][0].update(changes)
    assert validate_review(candidate, source, draft())


def test_long_necessary_comment_passes_without_length_gate(source):
    long_command = "run-original --strict " + "--input example " * 200
    original = {**review()["comment"], "commands": [long_command]}
    candidate = {**review(), "findings": [], "comment": copy.deepcopy(original)}
    assert validate_review(candidate, source, original) == []


def test_review_schema_preserves_public_comment_contract():
    public_schema = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
    before = copy.deepcopy(public_schema)
    schema = review_schema(public_schema)
    assert schema["properties"]["comment"] == public_schema == before
    assert set(schema["required"]) == {"required_facts", "findings", "comment"}
    schema["properties"]["comment"]["properties"]["local"] = {"type": "string"}
    assert public_schema == before
