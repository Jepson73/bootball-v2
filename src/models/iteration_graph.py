"""
src/models/iteration_graph.py

Graph data generation for model lifecycle visualization.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class IterationGraph:
    """Generates graph-ready data from model iterations.

    Produces data suitable for charting libraries (Chart.js, etc.)
    """

    @staticmethod
    def brier_score_timeline(iterations: list, retrain_events: list[dict]) -> dict[str, Any]:
        """Generate Brier score timeline graph data.

        Returns dict with labels, datasets for charting.
        """
        if not iterations:
            return {"labels": [], "datasets": []}

        sorted_iterations = sorted(iterations, key=lambda x: x.version_number)

        labels = [f"v{i.version_number}" for i in sorted_iterations]
        brier_scores = [i.brier_score for i in sorted_iterations]

        datasets = [
            {
                "label": "Brier Score",
                "data": brier_scores,
                "borderColor": "rgb(75, 192, 192)",
                "backgroundColor": "rgba(75, 192, 192, 0.1)",
                "tension": 0.3,
                "fill": True,
            }
        ]

        retrain_markers = []
        for i, iteration in enumerate(sorted_iterations):
            for event in retrain_events:
                if event.get("new_version_id"):
                    retrain_markers.append(i)

        if retrain_markers:
            marker_data = [None] * len(sorted_iterations)
            for idx in retrain_markers:
                marker_data[idx] = brier_scores[idx]
            datasets.append(
                {
                    "label": "Retrain Points",
                    "data": marker_data,
                    "borderColor": "rgb(255, 99, 132)",
                    "backgroundColor": "rgb(255, 99, 132)",
                    "pointRadius": 8,
                    "pointStyle": "triangle",
                    "showLine": False,
                }
            )

        return {"labels": labels, "datasets": datasets}

    @staticmethod
    def accuracy_timeline(iterations: list) -> dict[str, Any]:
        """Generate accuracy timeline graph data."""
        if not iterations:
            return {"labels": [], "datasets": []}

        sorted_iterations = sorted(iterations, key=lambda x: x.version_number)

        labels = [f"v{i.version_number}" for i in sorted_iterations]
        accuracies = [i.accuracy * 100 for i in sorted_iterations]

        return {
            "labels": labels,
            "datasets": [
                {
                    "label": "Accuracy %",
                    "data": accuracies,
                    "borderColor": "rgb(54, 162, 235)",
                    "backgroundColor": "rgba(54, 162, 235, 0.1)",
                    "tension": 0.3,
                    "fill": True,
                }
            ],
        }

    @staticmethod
    def calibration_comparison(iterations: list) -> dict[str, Any]:
        """Generate ECE (calibration error) comparison graph."""
        if not iterations:
            return {"labels": [], "datasets": []}

        sorted_iterations = sorted(iterations, key=lambda x: x.version_number)

        labels = [f"v{i.version_number}" for i in sorted_iterations]
        ece_values = [i.ece * 100 for i in sorted_iterations]

        return {
            "labels": labels,
            "datasets": [
                {
                    "label": "ECE %",
                    "data": ece_values,
                    "borderColor": "rgb(255, 159, 64)",
                    "backgroundColor": "rgba(255, 159, 64, 0.1)",
                    "tension": 0.3,
                    "fill": True,
                }
            ],
        }

    @staticmethod
    def drift_severity_timeline(
        iterations: list,
        retrain_events: list[dict],
        alert_threshold: float = 0.05
    ) -> dict[str, Any]:
        """Generate drift severity timeline with threshold bands."""
        if not iterations:
            return {"labels": [], "datasets": []}

        sorted_iterations = sorted(iterations, key=lambda x: x.version_number)

        labels = [f"v{i.version_number}" for i in sorted_iterations]

        baseline = sum(i.brier_score for i in sorted_iterations[-3:]) / min(3, len(sorted_iterations))
        drift_scores = [i.brier_score - baseline for i in sorted_iterations]

        datasets = [
            {
                "label": "Drift Score",
                "data": drift_scores,
                "borderColor": "rgb(153, 102, 255)",
                "backgroundColor": "rgba(153, 102, 255, 0.1)",
                "tension": 0.3,
                "fill": True,
            },
            {
                "label": "Alert Threshold",
                "data": [alert_threshold] * len(sorted_iterations),
                "borderColor": "rgb(255, 99, 132)",
                "borderDash": [5, 5],
                "pointRadius": 0,
                "fill": False,
            },
            {
                "label": "-Threshold",
                "data": [-alert_threshold] * len(sorted_iterations),
                "borderColor": "rgb(255, 99, 132)",
                "borderDash": [5, 5],
                "pointRadius": 0,
                "fill": False,
            },
        ]

        return {"labels": labels, "datasets": datasets}

    @staticmethod
    def retrain_impact_chart(retrain_events: list[dict]) -> dict[str, Any]:
        """Generate chart showing impact of retraining events.

        Shows Brier score before/after each retrain.
        """
        if not retrain_events:
            return {"labels": [], "datasets": []}

        labels = [f"Retrain {i+1}" for i in range(len(retrain_events))]

        before_scores = [e.get("brier_score_before", 0) or 0 for e in retrain_events]
        after_scores = [e.get("brier_score_after", 0) or 0 for e in retrain_events]

        return {
            "labels": labels,
            "datasets": [
                {
                    "label": "Before",
                    "data": before_scores,
                    "backgroundColor": "rgba(255, 99, 132, 0.7)",
                },
                {
                    "label": "After",
                    "data": after_scores,
                    "backgroundColor": "rgba(75, 192, 192, 0.7)",
                },
            ],
        }

    @staticmethod
    def sample_size_timeline(iterations: list) -> dict[str, Any]:
        """Generate sample size growth chart."""
        if not iterations:
            return {"labels": [], "datasets": []}

        sorted_iterations = sorted(iterations, key=lambda x: x.version_number)

        labels = [f"v{i.version_number}" for i in sorted_iterations]
        sample_sizes = [i.sample_size for i in sorted_iterations]

        return {
            "labels": labels,
            "datasets": [
                {
                    "label": "Sample Size",
                    "data": sample_sizes,
                    "borderColor": "rgb(75, 192, 192)",
                    "backgroundColor": "rgba(75, 192, 192, 0.5)",
                    "fill": True,
                }
            ],
        }


def generate_all_graphs(tracker, market: str) -> dict[str, Any]:
    """Generate all graph data for a market.

    Returns dict of graph_type -> graph_data.
    """
    lifecycle = tracker.get_lifecycle_graph()

    graph_generator = IterationGraph()

    return {
        "brier_score": graph_generator.brier_score_timeline(
            lifecycle.iterations, lifecycle.retrain_events
        ),
        "accuracy": graph_generator.accuracy_timeline(lifecycle.iterations),
        "calibration": graph_generator.calibration_comparison(lifecycle.iterations),
        "drift": graph_generator.drift_severity_timeline(
            lifecycle.iterations, lifecycle.retrain_events
        ),
        "retrain_impact": graph_generator.retrain_impact_chart(lifecycle.retrain_events),
        "sample_size": graph_generator.sample_size_timeline(lifecycle.iterations),
        "summary": {
            "market": market,
            "total_iterations": len(lifecycle.iterations),
            "total_retrains": len(lifecycle.retrain_events),
            "current_brier": lifecycle.current_brier,
            "baseline_brier": lifecycle.baseline_brier,
            "drift_score": lifecycle.drift_score,
            "overall_trend": lifecycle.overall_trend,
        },
    }
