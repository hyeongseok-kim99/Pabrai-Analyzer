from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


def _num(info: dict[str, Any], key: str):
    value = info.get(key)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return value


def _ratio(a, b):
    if a is None or b in (None, 0):
        return None
    try:
        return float(a) / float(b)
    except Exception:
        return None


def _piecewise_high(value, bands):
    """Higher is better. bands = [(threshold, score), ...] descending threshold."""
    if value is None:
        return None
    for threshold, score in bands:
        if value >= threshold:
            return score
    return 0.0


def _piecewise_low(value, bands):
    """Lower is better. bands = [(threshold, score), ...] ascending threshold."""
    if value is None:
        return None
    for threshold, score in bands:
        if value <= threshold:
            return score
    return 0.0


def _bounded(value, low=0.0, high=100.0):
    if value is None:
        return None
    return max(low, min(high, float(value)))


def _score_debt_to_equity(info):
    value = _num(info, "debtToEquity")
    if value is None:
        return None
    if value < 0:
        return 0.0
    return _piecewise_low(
        value,
        [(30, 100), (60, 90), (100, 75), (150, 55), (250, 30), (400, 10)],
    )


def _score_current_ratio(info):
    value = _num(info, "currentRatio")
    return _piecewise_high(
        value,
        [(2.0, 100), (1.5, 90), (1.2, 75), (1.0, 55), (0.8, 25), (0.6, 10)],
    )


def _score_net_debt_ebitda(info):
    debt = _num(info, "totalDebt")
    cash = _num(info, "totalCash")
    ebitda = _num(info, "ebitda")
    if debt is None or ebitda is None or ebitda <= 0:
        return None if debt is None or ebitda is None else 0.0
    net_debt = debt - (cash or 0.0)
    value = net_debt / ebitda
    return _piecewise_low(
        value,
        [(0, 100), (0.5, 95), (1.0, 90), (2.0, 75), (3.0, 55), (4.0, 30), (6.0, 10)],
    )


def _score_cash_to_debt(info):
    cash = _num(info, "totalCash")
    debt = _num(info, "totalDebt")
    if cash is None or debt is None:
        return None
    if debt <= 0:
        return 100.0
    value = cash / debt
    return _piecewise_high(
        value,
        [(1.5, 100), (1.0, 90), (0.6, 75), (0.35, 55), (0.2, 30), (0.1, 10)],
    )


def _score_fcf_positive(info):
    fcf = _num(info, "freeCashflow")
    if fcf is None:
        return None
    return 100.0 if fcf > 0 else 0.0


def _score_gross_margin(info):
    value = _num(info, "grossMargins")
    return _piecewise_high(
        value,
        [(0.60, 100), (0.45, 90), (0.35, 75), (0.25, 60), (0.15, 40), (0.08, 20)],
    )


def _score_operating_margin(info):
    value = _num(info, "operatingMargins")
    return _piecewise_high(
        value,
        [(0.30, 100), (0.22, 90), (0.15, 75), (0.10, 60), (0.05, 40), (0.0, 20)],
    )


def _score_roe(info):
    value = _num(info, "returnOnEquity")
    return _piecewise_high(
        value,
        [(0.30, 100), (0.22, 90), (0.15, 75), (0.10, 60), (0.05, 40), (0.0, 20)],
    )


def _score_roa(info):
    value = _num(info, "returnOnAssets")
    return _piecewise_high(
        value,
        [(0.15, 100), (0.10, 90), (0.07, 75), (0.04, 60), (0.02, 40), (0.0, 20)],
    )


def _score_revenue_growth(info):
    value = _num(info, "revenueGrowth")
    return _piecewise_high(
        value,
        [(0.20, 100), (0.12, 90), (0.07, 75), (0.03, 60), (0.0, 45), (-0.05, 25)],
    )


def _score_earnings_growth(info):
    value = _num(info, "earningsGrowth")
    return _piecewise_high(
        value,
        [(0.20, 100), (0.12, 90), (0.07, 75), (0.03, 60), (0.0, 45), (-0.10, 20)],
    )


def _score_fcf_margin(info):
    fcf = _num(info, "freeCashflow")
    revenue = _num(info, "totalRevenue")
    value = _ratio(fcf, revenue)
    return _piecewise_high(
        value,
        [(0.20, 100), (0.15, 90), (0.10, 75), (0.06, 60), (0.03, 40), (0.0, 20)],
    )


def _score_cash_conversion(info):
    ocf = _num(info, "operatingCashflow")
    net_income = _num(info, "netIncomeToCommon")
    if net_income is None:
        net_income = _num(info, "netIncome")
    value = _ratio(ocf, net_income)
    if value is None:
        return None
    if net_income is not None and net_income <= 0:
        return 20.0 if (ocf or 0) > 0 else 0.0
    return _piecewise_high(
        value,
        [(1.5, 100), (1.1, 90), (0.9, 75), (0.7, 55), (0.5, 30), (0.3, 10)],
    )


def _score_insider_alignment(info):
    value = _num(info, "heldPercentInsiders")
    if value is None:
        return None
    # This is only an ownership-alignment proxy, not a management-quality judgment.
    return _piecewise_high(
        value,
        [(0.20, 100), (0.10, 90), (0.05, 75), (0.02, 60), (0.005, 45), (0.0, 30)],
    )


def _score_pe(info):
    value = _num(info, "trailingPE")
    if value is None or value <= 0:
        return None
    return _piecewise_low(
        value,
        [(10, 100), (15, 90), (20, 75), (25, 60), (35, 40), (50, 20), (80, 5)],
    )


def _score_ev_ebitda(info):
    value = _num(info, "enterpriseToEbitda")
    if value is None or value <= 0:
        return None
    return _piecewise_low(
        value,
        [(7, 100), (10, 90), (13, 75), (16, 60), (22, 40), (30, 20), (45, 5)],
    )


def _score_fcf_yield(info):
    fcf = _num(info, "freeCashflow")
    market_cap = _num(info, "marketCap")
    value = _ratio(fcf, market_cap)
    return _piecewise_high(
        value,
        [(0.10, 100), (0.07, 90), (0.05, 75), (0.035, 60), (0.02, 40), (0.0, 15)],
    )


def _score_price_to_book(info):
    value = _num(info, "priceToBook")
    if value is None or value <= 0:
        return None
    return _piecewise_low(
        value,
        [(1.0, 100), (1.5, 90), (2.5, 75), (4.0, 60), (7.0, 40), (12.0, 20), (20.0, 5)],
    )


def _score_beta(info):
    value = _num(info, "beta")
    if value is None or value < 0:
        return None
    return _piecewise_low(
        value,
        [(0.7, 100), (0.9, 90), (1.1, 75), (1.3, 60), (1.6, 40), (2.0, 20), (3.0, 5)],
    )


def _score_failure_profit(info):
    profit_margin = _num(info, "profitMargins")
    if profit_margin is None:
        return None
    if profit_margin > 0.12:
        return 100.0
    if profit_margin > 0.05:
        return 80.0
    if profit_margin > 0:
        return 60.0
    if profit_margin > -0.05:
        return 25.0
    return 0.0


@dataclass(frozen=True)
class AutoFactor:
    key: str
    category: str
    label: str
    weight_pct: float
    scorer: Callable[[dict[str, Any]], float | None]
    note: str


FACTORS = [
    AutoFactor("debt_to_equity", "Leverage", "부채/자기자본", 5.0, _score_debt_to_equity, "낮을수록 우수"),
    AutoFactor("current_ratio", "Leverage", "유동비율", 4.0, _score_current_ratio, "단기 유동성"),
    AutoFactor("net_debt_ebitda", "Leverage", "순부채/EBITDA", 6.0, _score_net_debt_ebitda, "낮을수록 우수"),
    AutoFactor("cash_to_debt", "Leverage", "현금/부채", 4.0, _score_cash_to_debt, "높을수록 우수"),
    AutoFactor("positive_fcf", "Leverage", "FCF 양수 여부", 5.0, _score_fcf_positive, "현금창출력"),

    AutoFactor("gross_margin", "Moat", "매출총이익률", 7.0, _score_gross_margin, "Moat의 정량 프록시"),
    AutoFactor("operating_margin", "Moat", "영업이익률", 6.0, _score_operating_margin, "가격결정력/원가구조 프록시"),
    AutoFactor("roe_moat", "Moat", "ROE", 5.0, _score_roe, "자본효율 프록시"),
    AutoFactor("roa_moat", "Moat", "ROA", 4.0, _score_roa, "자산효율 프록시"),

    AutoFactor("roe_management", "Management & Ownership", "ROE 기반 자본배분 프록시", 6.0, _score_roe, "경영진 평가의 일부만 대체"),
    AutoFactor("roa_management", "Management & Ownership", "ROA 기반 운용효율 프록시", 5.0, _score_roa, "경영진 평가의 일부만 대체"),
    AutoFactor("earnings_growth", "Management & Ownership", "이익 성장", 5.0, _score_earnings_growth, "자본배분 결과 프록시"),
    AutoFactor("insider_alignment", "Management & Ownership", "내부자 지분", 4.0, _score_insider_alignment, "이해관계 정렬 프록시"),

    AutoFactor("revenue_growth", "Business Economics", "매출 성장", 2.5, _score_revenue_growth, "사업 경제성"),
    AutoFactor("fcf_margin", "Business Economics", "FCF 마진", 3.0, _score_fcf_margin, "현금 경제성"),
    AutoFactor("operating_margin_business", "Business Economics", "영업이익률", 2.5, _score_operating_margin, "사업 경제성"),

    AutoFactor("cash_conversion", "Accounting", "영업현금흐름/순이익", 3.0, _score_cash_conversion, "이익의 질"),
    AutoFactor("fcf_quality", "Accounting", "FCF 마진", 2.0, _score_fcf_margin, "현금 전환 품질"),

    AutoFactor("pe", "Valuation", "Trailing P/E", 2.0, _score_pe, "단순 가치평가 프록시"),
    AutoFactor("ev_ebitda", "Valuation", "EV/EBITDA", 2.0, _score_ev_ebitda, "단순 가치평가 프록시"),
    AutoFactor("fcf_yield", "Valuation", "FCF Yield", 2.0, _score_fcf_yield, "높을수록 우수"),
    AutoFactor("price_to_book", "Valuation", "P/B", 1.0, _score_price_to_book, "산업별 해석 필요"),

    AutoFactor("beta", "External Risks", "Beta", 3.0, _score_beta, "시장 민감도 프록시"),

    AutoFactor("failure_profitability", "Failure Points", "지속 손실 위험", 2.5, _score_failure_profit, "영구손실 가능성 프록시"),
    AutoFactor("failure_fcf", "Failure Points", "FCF 실패점", 2.5, _score_fcf_positive, "현금창출 실패 여부"),
]

# 4% Circle of Competence + 2% Personal Biases are intentionally NOT auto-scored.
# Qualitative parts of Moat / Management are only proxies, never treated as definitive.


def compute_pabrai_auto_score(info: dict[str, Any]) -> dict[str, Any]:
    factor_rows = []
    weighted_sum = 0.0
    covered_weight = 0.0

    category_acc = {}

    for factor in FACTORS:
        score = factor.scorer(info)

        category_acc.setdefault(
            factor.category,
            {"weighted": 0.0, "covered": 0.0},
        )

        if score is not None:
            score = _bounded(score)
            weighted_sum += score * factor.weight_pct
            covered_weight += factor.weight_pct
            category_acc[factor.category]["weighted"] += score * factor.weight_pct
            category_acc[factor.category]["covered"] += factor.weight_pct

        factor_rows.append(
            {
                "key": factor.key,
                "category": factor.category,
                "label": factor.label,
                "weight_pct": factor.weight_pct,
                "score": None if score is None else round(score, 2),
                "note": factor.note,
            }
        )

    total_score = None
    if covered_weight > 0:
        total_score = weighted_sum / covered_weight

    category_scores = {}
    for category, acc in category_acc.items():
        if acc["covered"] > 0:
            category_scores[category] = round(
                acc["weighted"] / acc["covered"],
                2,
            )
        else:
            category_scores[category] = None

    coverage_pct = covered_weight  # factor weights are percentage points out of 100.

    if coverage_pct >= 70:
        status = "AUTO_PRELIM"
    elif coverage_pct >= 45:
        status = "LOW_COVERAGE"
    else:
        status = "INSUFFICIENT_DATA"

    return {
        "total_score": None if total_score is None else round(total_score, 2),
        "coverage_pct": round(coverage_pct, 2),
        "status": status,
        "category_scores": category_scores,
        "factors": factor_rows,
        "raw_metrics": {
            key: info.get(key)
            for key in [
                "marketCap",
                "totalRevenue",
                "freeCashflow",
                "operatingCashflow",
                "totalDebt",
                "totalCash",
                "ebitda",
                "debtToEquity",
                "currentRatio",
                "grossMargins",
                "operatingMargins",
                "profitMargins",
                "returnOnAssets",
                "returnOnEquity",
                "revenueGrowth",
                "earningsGrowth",
                "heldPercentInsiders",
                "trailingPE",
                "enterpriseToEbitda",
                "priceToBook",
                "beta",
            ]
        },
        "method_note": (
            "자동 점수는 공개 재무/시장 데이터 기반의 정량 프록시입니다. "
            "Circle of Competence, Personal Biases 및 경영진/해자에 대한 "
            "정성 판단을 완전히 대체하지 않습니다."
        ),
    }
