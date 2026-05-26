"""
Tests for ranking algorithms and scoring engine.

Target functions:
- app.llm_judge_score(agent_id) -> dict
- app.compute_ranking(metrics) -> list
- app.filter_by_time_range(metrics, start, end) -> list
- app.generate_csv_report(ranking) -> str
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import (
    llm_judge_score,
    compute_ranking,
    filter_by_time_range,
    generate_csv_report,
    LLM_JUDGE_DIMENSIONS,
    AGENT_NAMES,
)

# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def sample_metrics():
    """Sample metrics data simulating data/metrics_data.json structure."""
    return [
        {
            "agent_id": "0e687182-0e7f-4a77-af6c-41a1dedcba64",
            "agent_name": "Hermes",
            "tcr": 0.95,
            "fpsr": 0.02,
            "art_hours": 1.5,
            "tcd_hours": 2.0,
            "rr": 0.98,
            "llm_score": 85,
            "llm_dimensions": {
                "task_completion": 90,
                "code_quality": 85,
                "response_speed": 80,
                "architecture": 85,
                "analysis_depth": 85,
            },
            "composite_score": 85.0,
            "objective_score": 85.0,
        },
        {
            "agent_id": "50443896-7a01-4f22-aa16-111dd8e052da",
            "agent_name": "Codex",
            "tcr": 0.88,
            "fpsr": 0.05,
            "art_hours": 2.0,
            "tcd_hours": 3.0,
            "rr": 0.92,
            "llm_score": 78,
            "llm_dimensions": {
                "task_completion": 82,
                "code_quality": 76,
                "response_speed": 75,
                "architecture": 80,
                "analysis_depth": 77,
            },
            "composite_score": 78.0,
            "objective_score": 78.0,
        },
        {
            "agent_id": "93609087-b5a5-4c10-9478-a9216123d8eb",
            "agent_name": "Claude Code",
            "tcr": 0.91,
            "fpsr": 0.03,
            "art_hours": 1.8,
            "tcd_hours": 2.5,
            "rr": 0.95,
            "llm_score": 82,
            "llm_dimensions": {
                "task_completion": 88,
                "code_quality": 80,
                "response_speed": 78,
                "architecture": 82,
                "analysis_depth": 82,
            },
            "composite_score": 82.0,
            "objective_score": 82.0,
        },
    ]


@pytest.fixture
def empty_metrics():
    return []


class TestComputeRanking:
    """Tests for compute_ranking()."""

    def test_returns_list(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        assert isinstance(result, list)

    def test_returns_correct_count(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        assert len(result) == len(sample_metrics)

    def test_sorted_by_composite_desc(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        scores = [r["composite_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_rank_numbers_are_consecutive(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        ranks = [r["rank"] for r in result]
        assert ranks == list(range(1, len(result) + 1))

    def test_rank_key_present(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        for r in result:
            assert "rank" in r
            assert isinstance(r["rank"], int)

    def test_empty_input(self, empty_metrics):
        result = compute_ranking(empty_metrics)
        assert result == []

    def test_required_keys(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        for r in result:
            for key in ["agent_id", "agent_name", "composite_score", "rank", "tcr", "fpsr", "rr"]:
                assert key in r, f"Missing key: {key}"

    def test_composite_score_type(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        for r in result:
            assert isinstance(r["composite_score"], float)

    def test_agent_id_retained(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        agent_ids = [r["agent_id"] for r in result]
        for m in sample_metrics:
            assert m["agent_id"] in agent_ids

    def test_ranking_order_matches_input_counts(self, sample_metrics):
        result = compute_ranking(sample_metrics)
        original_ids = {m["agent_id"] for m in sample_metrics}
        result_ids = {r["agent_id"] for r in result}
        assert original_ids == result_ids


class TestFilterByTimeRange:
    """Tests for filter_by_time_range()."""

    def test_no_filter_returns_all(self, sample_metrics):
        result = filter_by_time_range(sample_metrics, None, None)
        assert len(result) == len(sample_metrics)

    def test_empty_list(self, empty_metrics):
        result = filter_by_time_range(empty_metrics, "2026-01-01", "2026-12-31")
        assert result == []

    def test_none_params(self, sample_metrics):
        result = filter_by_time_range(sample_metrics, None, "")
        assert len(result) == len(sample_metrics)

    def test_all_none_strings(self, sample_metrics):
        result = filter_by_time_range(sample_metrics, "", "")
        assert len(result) == len(sample_metrics)


class TestGenerateCsvReport:
    """Tests for generate_csv_report()."""

    def test_returns_string(self, sample_metrics):
        ranking = compute_ranking(sample_metrics)
        result = generate_csv_report(ranking)
        assert isinstance(result, str)

    def test_contains_ranking_header(self, sample_metrics):
        ranking = compute_ranking(sample_metrics)
        result = generate_csv_report(ranking)
        assert "排名" in result or "rank" in result.lower()

    def test_contains_agent_names(self, sample_metrics):
        ranking = compute_ranking(sample_metrics)
        result = generate_csv_report(ranking)
        assert "Hermes" in result

    def test_parsable_as_csv(self, sample_metrics):
        import csv, io
        ranking = compute_ranking(sample_metrics)
        result = generate_csv_report(ranking)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == len(ranking)

    def test_has_correct_column_count(self, sample_metrics):
        import csv, io
        ranking = compute_ranking(sample_metrics)
        result = generate_csv_report(ranking)
        reader = csv.DictReader(io.StringIO(result))
        rows = list(reader)
        for row in rows:
            assert len(row) >= 8, f"Expected at least 8 columns, got {len(row)}"

    def test_empty_ranking(self, empty_metrics):
        ranking = compute_ranking(empty_metrics)
        result = generate_csv_report(ranking)
        assert result != ""


class TestLlmJudgeScore:
    """Tests for llm_judge_score()."""

    def test_returns_dict(self):
        result = llm_judge_score("0e687182-0e7f-4a77-af6c-41a1dedcba64")
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        result = llm_judge_score("0e687182-0e7f-4a77-af6c-41a1dedcba64")
        required_keys = {"agent_id", "agent_name", "dimensions", "comments"}
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_dimensions_are_within_range(self):
        result = llm_judge_score("0e687182-0e7f-4a77-af6c-41a1dedcba64")
        dims = result["dimensions"]
        for dim in LLM_JUDGE_DIMENSIONS:
            assert dim in dims, f"Missing dimension: {dim}"
            val = dims[dim]
            assert 0 <= val <= 100, f"Dimension {dim} value {val} outside 0-100 range"

    def test_all_dimensions_present(self):
        result = llm_judge_score("0e687182-0e7f-4a77-af6c-41a1dedcba64")
        dims = result["dimensions"]
        assert len(dims) == len(LLM_JUDGE_DIMENSIONS)

    def test_known_agent_returns_name(self):
        result = llm_judge_score("0e687182-0e7f-4a77-af6c-41a1dedcba64")
        if result["agent_id"] in AGENT_NAMES:
            assert result["agent_name"] == AGENT_NAMES[result["agent_id"]]

    def test_unknown_agent(self):
        result = llm_judge_score("unknown-agent-12345")
        assert result is not None
        assert result["agent_name"] == "unknown-"

    def test_comments_are_strings(self):
        result = llm_judge_score("0e687182-0e7f-4a77-af6c-41a1dedcba64")
        assert isinstance(result["comments"], list)
        assert len(result["comments"]) > 0
        assert all(isinstance(c, str) for c in result["comments"])

    def test_all_agents_have_data(self):
        for agent_id in AGENT_NAMES:
            result = llm_judge_score(agent_id)
            assert result is not None


class TestIntegration:
    """Integration tests across multiple functions."""

    def test_full_pipeline(self, sample_metrics):
        """Score -> Rank -> CSV end-to-end."""
        for m in sample_metrics:
            score = llm_judge_score(m["agent_id"])
            assert score is not None
        ranking = compute_ranking(sample_metrics)
        assert len(ranking) == len(sample_metrics)
        csv_str = generate_csv_report(ranking)
        assert isinstance(csv_str, str)
        assert len(csv_str) > 0

    def test_multi_agent_pipeline(self):
        """Full pipeline with real loaded data."""
        from app import load_metrics_data
        metrics = load_metrics_data().get("metrics", [])
        if len(metrics) >= 2:
            ranking = compute_ranking(metrics)
            assert len(ranking) >= 2
            csv_str = generate_csv_report(ranking)
            assert csv_str
