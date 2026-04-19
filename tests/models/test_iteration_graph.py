"""
tests/models/test_iteration_graph.py

Tests for iteration graph data generation.
"""
import sys
sys.path.insert(0, '.')

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from src.models.iteration_graph import (
    IterationGraph,
    generate_all_graphs,
)
from src.models.model_tracker import ModelIteration, LifecycleGraph


class TestIterationGraph:
    """Tests for IterationGraph class."""

    def test_brier_score_timeline_empty(self):
        """Test empty iterations return empty graph."""
        result = IterationGraph.brier_score_timeline([], [])
        assert result["labels"] == []
        assert result["datasets"] == []

    def test_brier_score_timeline_single(self):
        """Test single iteration."""
        iterations = [
            ModelIteration(1, None, 0.22, 0.65, 0.04, 5000, True, datetime.utcnow(), [])
        ]
        result = IterationGraph.brier_score_timeline(iterations, [])

        assert result["labels"] == ["v1"]
        assert len(result["datasets"]) == 1
        assert result["datasets"][0]["label"] == "Brier Score"
        assert result["datasets"][0]["data"] == [0.22]

    def test_brier_score_timeline_with_retrains(self):
        """Test timeline with retrain markers."""
        iterations = [
            ModelIteration(1, None, 0.25, 0.60, 0.06, 5000, False, datetime.utcnow(), []),
            ModelIteration(2, None, 0.22, 0.65, 0.04, 6000, False, datetime.utcnow(), []),
            ModelIteration(3, None, 0.21, 0.66, 0.03, 7000, True, datetime.utcnow(), []),
        ]
        retrain_events = [
            {"new_version_id": 2, "brier_score_before": 0.25, "brier_score_after": 0.22},
        ]
        result = IterationGraph.brier_score_timeline(iterations, retrain_events)

        assert len(result["labels"]) == 3
        assert len(result["datasets"]) == 2
        assert result["datasets"][1]["label"] == "Retrain Points"

    def test_accuracy_timeline(self):
        """Test accuracy timeline generation."""
        iterations = [
            ModelIteration(1, None, 0.25, 0.60, 0.05, 5000, False, datetime.utcnow(), []),
            ModelIteration(2, None, 0.22, 0.65, 0.04, 6000, True, datetime.utcnow(), []),
        ]
        result = IterationGraph.accuracy_timeline(iterations)

        assert result["labels"] == ["v1", "v2"]
        assert result["datasets"][0]["data"] == [60, 65]

    def test_calibration_comparison(self):
        """Test ECE comparison chart."""
        iterations = [
            ModelIteration(1, None, 0.25, 0.60, 0.06, 5000, False, datetime.utcnow(), []),
            ModelIteration(2, None, 0.22, 0.65, 0.03, 6000, True, datetime.utcnow(), []),
        ]
        result = IterationGraph.calibration_comparison(iterations)

        assert result["labels"] == ["v1", "v2"]
        assert result["datasets"][0]["data"] == [6, 3]

    def test_drift_severity_timeline(self):
        """Test drift severity with threshold bands."""
        iterations = [
            ModelIteration(1, None, 0.22, 0.65, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(2, None, 0.22, 0.65, 0.04, 5000, False, datetime.utcnow(), []),
            ModelIteration(3, None, 0.22, 0.65, 0.04, 5000, True, datetime.utcnow(), []),
        ]
        result = IterationGraph.drift_severity_timeline(iterations, [], alert_threshold=0.05)

        assert len(result["datasets"]) == 3
        assert result["datasets"][1]["label"] == "Alert Threshold"
        assert result["datasets"][1]["data"] == [0.05, 0.05, 0.05]

    def test_retrain_impact_chart(self):
        """Test retrain impact before/after chart."""
        retrain_events = [
            {
                "brier_score_before": 0.28,
                "brier_score_after": 0.22,
            },
            {
                "brier_score_before": 0.25,
                "brier_score_after": 0.21,
            },
        ]
        result = IterationGraph.retrain_impact_chart(retrain_events)

        assert result["labels"] == ["Retrain 1", "Retrain 2"]
        assert result["datasets"][0]["label"] == "Before"
        assert result["datasets"][0]["data"] == [0.28, 0.25]
        assert result["datasets"][1]["data"] == [0.22, 0.21]

    def test_sample_size_timeline(self):
        """Test sample size growth chart."""
        iterations = [
            ModelIteration(1, None, 0.25, 0.60, 0.05, 5000, False, datetime.utcnow(), []),
            ModelIteration(2, None, 0.22, 0.65, 0.04, 8000, False, datetime.utcnow(), []),
            ModelIteration(3, None, 0.21, 0.66, 0.03, 12000, True, datetime.utcnow(), []),
        ]
        result = IterationGraph.sample_size_timeline(iterations)

        assert result["labels"] == ["v1", "v2", "v3"]
        assert result["datasets"][0]["data"] == [5000, 8000, 12000]


class TestGenerateAllGraphs:
    """Tests for generate_all_graphs function."""

    def test_generate_all_graphs_empty(self):
        """Test with no data."""
        mock_tracker = MagicMock()
        mock_tracker.get_lifecycle_graph.return_value = LifecycleGraph(
            market="btts",
            iterations=[],
            retrain_events=[],
            current_brier=0,
            baseline_brier=0,
            drift_score=0,
            overall_trend="insufficient_data",
        )

        result = generate_all_graphs(mock_tracker, "btts")

        assert "brier_score" in result
        assert "accuracy" in result
        assert "calibration" in result
        assert "drift" in result
        assert "retrain_impact" in result
        assert "sample_size" in result
        assert "summary" in result

        assert result["summary"]["market"] == "btts"
        assert result["summary"]["total_iterations"] == 0
