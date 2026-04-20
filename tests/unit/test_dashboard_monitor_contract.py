from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_TSX = ROOT / "frontend" / "src" / "pages" / "DashboardPage.tsx"


def _src() -> str:
    return DASHBOARD_TSX.read_text(encoding="utf-8")


def test_usage_tooltip_sorts_descending():
    src = _src()
    assert "sort((a, b) => b.value - a.value)" in src


def test_axis_uses_dynamic_percent_formatter():
    src = _src()
    assert "tickFormatter={(v: number) => fmtAxisPercent(v, ramScale, monitorLogCoef)}" in src
    assert "tickFormatter={(v: number) => fmtAxisPercent(v, cpuScale, monitorLogCoef)}" in src


def test_log_linear_transforms_present():
    src = _src()
    assert "function percentToPlot(valuePct: number, scale: 'log' | 'linear', coef: LogCoef)" in src
    assert "function plotToPercent(value: number, scale: 'log' | 'linear', coef: LogCoef)" in src
    assert "function invLogPlotPercent(plotPct: number, coef: LogCoef)" in src


def test_dashboard_includes_mic_v7_runtime_mapping_panel():
    src = _src()
    assert "api.health.micOrchestratorV7" in src
    assert "MIC Orchestrator V7 runtime mapping" in src
