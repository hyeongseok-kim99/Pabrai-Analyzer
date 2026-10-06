import re
from datetime import date

import FinanceDataReader as fdr
import pandas as pd
import streamlit as st
from supabase import create_client

# =========================================================
# Page config
# =========================================================
st.set_page_config(
    page_title="Pabrai Analyzer",
    page_icon="📊",
    layout="wide",
)

CORE_CATEGORIES = {
    "Leverage",
    "Moat",
    "Management & Ownership",
}

SCORE_OPTIONS = ["미평가", "N/A", "0", "1", "2", "3", "4", "5"]


# =========================================================
# Supabase
# =========================================================
@st.cache_resource
def get_supabase():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SECRET_KEY"],
    )


supabase = get_supabase()


# =========================================================
# Basic data helpers
# =========================================================
def get_questions():
    result = (
        supabase
        .table("questions")
        .select("*")
        .eq("is_active", True)
        .order("question_no")
        .execute()
    )
    return result.data or []


def get_categories():
    result = (
        supabase
        .table("categories")
        .select("*")
        .order("display_order")
        .execute()
    )
    return result.data or []


def get_company_by_ticker(ticker):
    result = (
        supabase
        .table("companies")
        .select("*")
        .eq("ticker", ticker)
        .limit(20)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def get_analyses(limit=300):
    result = (
        supabase
        .table("analyses")
        .select(
            """
            id,
            company_id,
            version,
            analysis_date,
            status,
            total_score,
            decision,
            notes,
            created_at,
            companies (
                ticker,
                company_name,
                market,
                country
            )
            """
        )
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_next_version(company_id):
    result = (
        supabase
        .table("analyses")
        .select("version")
        .eq("company_id", company_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return 1
    return int(result.data[0]["version"]) + 1


def create_analysis(company_id):
    version = get_next_version(company_id)
    result = (
        supabase
        .table("analyses")
        .insert(
            {
                "company_id": company_id,
                "version": version,
                "analysis_date": str(date.today()),
                "status": "DRAFT",
            }
        )
        .execute()
    )
    if not result.data:
        raise RuntimeError("분석 생성 결과를 확인할 수 없습니다.")
    return result.data[0]


def get_answers(analysis_id):
    result = (
        supabase
        .table("answers")
        .select("*")
        .eq("analysis_id", analysis_id)
        .execute()
    )
    return result.data or []


def save_answer(
    analysis_id,
    question_no,
    score,
    status,
    note,
    evidence,
    evidence_url,
):
    payload = {
        "analysis_id": analysis_id,
        "question_no": question_no,
        "score": score,
        "status": status,
        "note": note.strip() if note else None,
        "evidence": evidence.strip() if evidence else None,
        "evidence_url": evidence_url.strip() if evidence_url else None,
    }
    (
        supabase
        .table("answers")
        .upsert(
            payload,
            on_conflict="analysis_id,question_no",
        )
        .execute()
    )


def update_analysis(
    analysis_id,
    *,
    total_score=None,
    status=None,
    decision=None,
    notes=None,
):
    payload = {}

    if total_score is not None:
        payload["total_score"] = round(float(total_score), 2)
    if status is not None:
        payload["status"] = status
    if decision is not None:
        payload["decision"] = decision
    if notes is not None:
        payload["notes"] = notes

    if payload:
        (
            supabase
            .table("analyses")
            .update(payload)
            .eq("id", analysis_id)
            .execute()
        )


# =========================================================
# Universe / auto-score helpers
# =========================================================
def get_active_universe():
    result = (
        supabase
        .table("investment_universe")
        .select(
            """
            company_id,
            universe,
            rank,
            as_of,
            companies (
                id,
                ticker,
                company_name,
                market,
                country
            )
            """
        )
        .eq("is_active", True)
        .limit(1000)
        .execute()
    )
    return result.data or []


def universe_counts(rows):
    counts = {"KOSPI_TOP200": 0, "SP500": 0}
    for row in rows:
        key = row.get("universe")
        if key in counts:
            counts[key] += 1
    return counts


def search_universe(rows, term="", universe="전체"):
    term = term.strip().lower()
    filtered = []

    for row in rows:
        if universe != "전체" and row.get("universe") != universe:
            continue

        company = row.get("companies") or {}
        ticker = str(company.get("ticker", "")).lower()
        name = str(company.get("company_name", "")).lower()

        if term and term not in ticker and term not in name:
            continue

        filtered.append(row)

    filtered.sort(
        key=lambda r: (
            0 if r.get("universe") == "KOSPI_TOP200" else 1,
            r.get("rank") or 999999,
        )
    )
    return filtered


def get_framework_scores(framework_id="PABRAI_AUTO"):
    result = (
        supabase
        .table("framework_scores")
        .select(
            """
            company_id,
            framework_id,
            total_score,
            coverage_pct,
            status,
            as_of,
            details,
            companies (
                ticker,
                company_name,
                market,
                country
            )
            """
        )
        .eq("framework_id", framework_id)
        .limit(1000)
        .execute()
    )
    return result.data or []


def get_frameworks():
    result = (
        supabase
        .table("frameworks")
        .select("*")
        .eq("is_active", True)
        .order("name")
        .execute()
    )
    return result.data or []


def get_framework_dimensions(framework_id):
    result = (
        supabase
        .table("framework_dimensions")
        .select("*")
        .eq("framework_id", framework_id)
        .order("display_order")
        .execute()
    )
    return result.data or []


# =========================================================
# Manual/custom company lookup
# =========================================================
@st.cache_data(ttl=86400, show_spinner=False)
def load_listing(name):
    return fdr.StockListing(name)


def _find_col(df, names):
    for name in names:
        if name in df.columns:
            return name
    return None


def _clean(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip()
    return text or None


def validate_symbol_input(raw_value):
    value = raw_value.strip()

    if not value:
        return None, None, "종목코드 또는 Ticker를 입력해주세요."

    if value.isdigit():
        if len(value) != 6:
            return None, None, "한국 종목코드는 정확히 6자리 숫자여야 합니다."
        return "KR", value, None

    if re.fullmatch(r"[A-Za-z]+", value):
        return "US", value.upper(), None

    return None, None, "한국은 6자리 숫자, 미국은 영문자만 입력할 수 있습니다."


def lookup_external_company(region, ticker):
    if region == "KR":
        df = load_listing("KRX")
        symbol_col = _find_col(df, ["Symbol", "Code", "Ticker"])
        name_col = _find_col(df, ["Name", "Company", "CompanyName"])
        market_col = _find_col(df, ["Market", "Exchange"])

        if not symbol_col or not name_col:
            raise RuntimeError("KRX 종목 목록 형식을 확인할 수 없습니다.")

        symbols = df[symbol_col].astype(str).str.zfill(6)
        matched = df[symbols == ticker]

        if matched.empty:
            return None

        row = matched.iloc[0]
        return {
            "ticker": ticker,
            "company_name": _clean(row.get(name_col)),
            "market": _clean(row.get(market_col)) if market_col else "KRX",
            "country": "South Korea",
        }

    for listing_name in ["S&P500", "NASDAQ", "NYSE", "AMEX"]:
        df = load_listing(listing_name)
        symbol_col = _find_col(df, ["Symbol", "Code", "Ticker"])
        name_col = _find_col(df, ["Name", "Company", "CompanyName"])

        if not symbol_col or not name_col:
            continue

        symbols = df[symbol_col].astype(str).str.upper()
        matched = df[symbols == ticker]

        if matched.empty:
            continue

        row = matched.iloc[0]
        return {
            "ticker": ticker,
            "company_name": _clean(row.get(name_col)),
            "market": listing_name,
            "country": "USA",
        }

    return None


def resolve_company(raw_value):
    region, ticker, error = validate_symbol_input(raw_value)
    if error:
        return None, False, error

    existing = get_company_by_ticker(ticker)
    if existing:
        return existing, False, None

    try:
        record = lookup_external_company(region, ticker)
    except Exception as e:
        return None, False, f"기업 정보 조회 오류: {e}"

    if not record:
        return None, False, f"{ticker} 종목을 찾지 못했습니다."

    try:
        result = (
            supabase
            .table("companies")
            .insert(record)
            .execute()
        )
        if not result.data:
            raise RuntimeError("기업 저장 결과 없음")
        return result.data[0], True, None
    except Exception as e:
        return None, False, f"기업 저장 오류: {e}"


# =========================================================
# Scoring helpers for manual 213 checklist
# =========================================================
def score_to_status(score_choice):
    if score_choice == "미평가":
        return None, "UNKNOWN"
    if score_choice == "N/A":
        return None, "N/A"

    score = int(score_choice)

    if score >= 4:
        return score, "PASS"
    if score >= 2:
        return score, "WARNING"
    return score, "FAIL"


def answer_to_score_choice(answer):
    if not answer:
        return "미평가"
    if answer.get("status") == "N/A":
        return "N/A"
    score = answer.get("score")
    return "미평가" if score is None else str(score)


def build_answer_map(answers):
    return {
        int(answer["question_no"]): answer
        for answer in answers
    }


def calculate_summary(questions, answers):
    answer_map = build_answer_map(answers)

    total_weighted = 0.0
    total_rated_weight = 0.0
    category_data = {}
    completed_count = 0
    fail_count = 0
    warning_count = 0
    pass_count = 0
    core_zero_items = []

    for question in questions:
        category = question["category"]
        weight = float(question["weight_pct"])
        qno = int(question["question_no"])

        category_data.setdefault(
            category,
            {
                "weighted": 0.0,
                "rated_weight": 0.0,
                "completed_count": 0,
                "total_count": 0,
            },
        )
        category_data[category]["total_count"] += 1

        answer = answer_map.get(qno)
        if not answer or answer.get("status") == "UNKNOWN":
            continue

        completed_count += 1
        category_data[category]["completed_count"] += 1

        if answer.get("status") == "N/A":
            continue

        score = answer.get("score")
        if score is None:
            continue

        score = int(score)
        weighted_value = (score / 5.0) * weight

        total_weighted += weighted_value
        total_rated_weight += weight
        category_data[category]["weighted"] += weighted_value
        category_data[category]["rated_weight"] += weight

        status = answer.get("status")
        if status == "PASS":
            pass_count += 1
        elif status == "WARNING":
            warning_count += 1
        elif status == "FAIL":
            fail_count += 1

        if score == 0 and category in CORE_CATEGORIES:
            core_zero_items.append(
                {
                    "question_no": qno,
                    "category": category,
                    "question": question["question"],
                }
            )

    overall_score = None
    if total_rated_weight > 0:
        overall_score = total_weighted / total_rated_weight * 100.0

    for data in category_data.values():
        if data["rated_weight"] > 0:
            data["score"] = data["weighted"] / data["rated_weight"] * 100.0
        else:
            data["score"] = None

    total_questions = len(questions)
    unknown_count = max(0, total_questions - completed_count)
    completion_pct = completed_count / total_questions * 100.0 if total_questions else 0.0

    return {
        "overall_score": overall_score,
        "category_data": category_data,
        "completed_count": completed_count,
        "unknown_count": unknown_count,
        "pass_count": pass_count,
        "warning_count": warning_count,
        "fail_count": fail_count,
        "core_zero_items": core_zero_items,
        "completion_pct": completion_pct,
    }


def suggested_decision(summary):
    score = summary["overall_score"]

    if score is None:
        return "REVIEW REQUIRED"
    if summary["core_zero_items"]:
        return "REVIEW REQUIRED"
    if score >= 80:
        return "GO"
    if score >= 65:
        return "WAIT"
    return "NO-GO"


def analysis_label(analysis):
    company = analysis.get("companies") or {}
    return (
        f"{company.get('company_name', '-')} "
        f"({company.get('ticker', '-')}) · "
        f"V{analysis.get('version', '-')} · "
        f"{analysis.get('status', '-')}"
    )


def analysis_selector(analyses, key, label="분석 선택"):
    if not analyses:
        return None

    mapping = {
        analysis_label(item): item
        for item in analyses
    }
    labels = list(mapping.keys())

    preferred_id = st.session_state.get("current_analysis_id")
    default_index = 0

    if preferred_id:
        for idx, label_text in enumerate(labels):
            if mapping[label_text]["id"] == preferred_id:
                default_index = idx
                break

    selected = st.selectbox(
        label,
        labels,
        index=default_index,
        key=key,
    )
    return mapping[selected]


# =========================================================
# Load
# =========================================================
st.title("Pabrai 213 Analyzer")
st.caption(
    "KOSPI 시가총액 상위 200 + S&P 500 · 자동 1차 스크리닝 + 213문항 수동 심층평가"
)

try:
    questions = get_questions()
    categories = get_categories()
    universe = get_active_universe()
    auto_scores = get_framework_scores()
    frameworks = get_frameworks()
except Exception as e:
    st.error("Supabase 데이터를 불러오지 못했습니다.")
    st.code(str(e))
    st.stop()

question_count = len(questions)
counts = universe_counts(universe)

(
    tab_dashboard,
    tab_universe,
    tab_new_analysis,
    tab_checklist,
    tab_result,
    tab_auto_scores,
    tab_frameworks,
) = st.tabs(
    [
        "📊 Dashboard",
        "🏢 기업 관리",
        "➕ 새 분석",
        "✅ Checklist",
        "📈 수동 분석 결과",
        "⭐ Auto Pabrai Score",
        "🧠 투자철학 Framework",
    ]
)


# =========================================================
# Dashboard
# =========================================================
with tab_dashboard:
    analyses = get_analyses()

    scored_ok = [
        row for row in auto_scores
        if row.get("total_score") is not None
        and row.get("status") != "ERROR"
    ]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("KOSPI 상위", counts["KOSPI_TOP200"])
    c2.metric("S&P 500", counts["SP500"])
    c3.metric("활성 Universe", len(universe))
    c4.metric("Auto Score 완료", f"{len(scored_ok)}/{len(universe)}")

    c1, c2, c3 = st.columns(3)
    c1.metric("213문항", question_count)
    c2.metric(
        "수동 분석 완료",
        sum(1 for a in analyses if a.get("status") == "COMPLETED"),
    )
    c3.metric(
        "수동 분석 중",
        sum(1 for a in analyses if a.get("status") == "DRAFT"),
    )

    st.info(
        "Auto Pabrai Score는 703개 기업의 1차 스크리닝용입니다. "
        "관심 기업은 '새 분석 → Checklist'에서 213개 문항으로 심층 평가할 수 있습니다."
    )

    if scored_ok:
        top_rows = sorted(
            scored_ok,
            key=lambda x: float(x["total_score"]),
            reverse=True,
        )[:20]

        st.subheader("Auto Pabrai Score 상위 20")
        st.dataframe(
            [
                {
                    "Ticker": (r.get("companies") or {}).get("ticker"),
                    "기업": (r.get("companies") or {}).get("company_name"),
                    "Score": r.get("total_score"),
                    "Coverage": f"{float(r.get('coverage_pct') or 0):.1f}%",
                    "상태": r.get("status"),
                    "기준일": r.get("as_of"),
                }
                for r in top_rows
            ],
            use_container_width=True,
            hide_index=True,
        )


# =========================================================
# Company management / 703 universe
# =========================================================
with tab_universe:
    st.subheader("활성 기업 Universe")

    c1, c2, c3 = st.columns(3)
    c1.metric("KOSPI 시총 상위", counts["KOSPI_TOP200"])
    c2.metric("S&P 500", counts["SP500"])
    c3.metric("합계", len(universe))

    st.caption(
        "KOSPI는 시가총액 기준 상위 200개 보통주 중심으로 유지합니다. "
        "이전 KOSPI 종목은 DB를 삭제하지 않고 활성 리스트에서 제외해 과거 분석 이력을 보존합니다."
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        term = st.text_input(
            "검색",
            placeholder="005930, AAPL, 삼성전자, Apple",
            key="universe_search",
        )
    with col2:
        universe_filter = st.selectbox(
            "Universe",
            ["전체", "KOSPI_TOP200", "SP500"],
        )

    rows = search_universe(
        universe,
        term=term,
        universe=universe_filter,
    )

    st.dataframe(
        [
            {
                "Rank": r.get("rank"),
                "Universe": r.get("universe"),
                "Ticker": (r.get("companies") or {}).get("ticker"),
                "기업명": (r.get("companies") or {}).get("company_name"),
                "Market": (r.get("companies") or {}).get("market"),
                "Country": (r.get("companies") or {}).get("country"),
                "기준일": r.get("as_of"),
            }
            for r in rows
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("Universe 외 기업 직접 추가")
    st.caption(
        "한국: 6자리 숫자 · 미국: 영문자만 입력. "
        "703개 기본 Universe에 없는 기업도 수동 심층분석용으로 추가할 수 있습니다."
    )

    with st.form("custom_company_form"):
        raw_symbol = st.text_input(
            "종목코드 / Ticker",
            placeholder="005930 또는 AAPL",
        )
        add_clicked = st.form_submit_button(
            "기업 확인/추가",
            type="primary",
            use_container_width=True,
        )

    if add_clicked:
        company, created, error = resolve_company(raw_symbol)
        if error:
            st.error(error)
        else:
            verb = "추가했습니다" if created else "이미 등록되어 있습니다"
            st.success(
                f"{company['company_name']} ({company['ticker']})을 {verb}."
            )


# =========================================================
# New manual analysis
# =========================================================
with tab_new_analysis:
    st.subheader("새 213문항 심층 분석")
    st.caption("한국 6자리 종목코드 또는 미국 영문 Ticker를 입력하세요.")

    raw = st.text_input(
        "분석할 종목",
        placeholder="005930 또는 AAPL",
        key="new_analysis_symbol",
    )

    if raw:
        company, _, error = resolve_company(raw)

        if error:
            st.error(error)
        else:
            company_id = company["id"]
            next_version = get_next_version(company_id)

            c1, c2, c3 = st.columns(3)
            c1.metric("기업", company.get("company_name"))
            c2.metric("Ticker", company.get("ticker"))
            c3.metric("시장", company.get("market"))

            st.info(f"새 분석은 Version {next_version}으로 생성됩니다.")

            if st.button(
                "새 분석 시작",
                type="primary",
                use_container_width=True,
            ):
                new_analysis = create_analysis(company_id)
                st.session_state["current_analysis_id"] = new_analysis["id"]
                st.success(
                    f"Version {new_analysis['version']} 생성 완료. Checklist 탭에서 평가하세요."
                )


# =========================================================
# Manual 213 checklist
# =========================================================
with tab_checklist:
    st.subheader("213 Checklist")

    analyses = get_analyses()

    if not analyses:
        st.info("먼저 '새 분석' 탭에서 분석을 생성해주세요.")
    else:
        selected_analysis = analysis_selector(
            analyses,
            key="checklist_analysis_selector",
        )
        analysis_id = selected_analysis["id"]
        st.session_state["current_analysis_id"] = analysis_id

        company = selected_analysis.get("companies") or {}
        answers = get_answers(analysis_id)
        answer_map = build_answer_map(answers)
        summary = calculate_summary(questions, answers)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("기업", company.get("ticker", "-"))
        c2.metric("Version", selected_analysis.get("version", "-"))
        c3.metric("완료", f"{summary['completed_count']}/{question_count}")
        c4.metric(
            "현재 점수",
            f"{summary['overall_score']:.1f}"
            if summary["overall_score"] is not None else "-",
        )

        st.progress(
            min(1.0, summary["completion_pct"] / 100),
            text=f"진행률 {summary['completion_pct']:.1f}%",
        )

        if summary["core_zero_items"]:
            st.error(
                f"Leverage / Moat / Management 핵심 영역에 "
                f"0점 문항 {len(summary['core_zero_items'])}개가 있습니다."
            )

        category_names = [c["category"] for c in categories]
        selected_category = st.selectbox(
            "Category",
            category_names,
            key="manual_category",
        )

        category_questions = [
            q for q in questions
            if q["category"] == selected_category
        ]

        category_summary = summary["category_data"].get(
            selected_category,
            {},
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("문항 수", len(category_questions))
        c2.metric(
            "카테고리 점수",
            f"{category_summary.get('score'):.1f}"
            if category_summary.get("score") is not None else "-",
        )
        c3.metric(
            "완료",
            f"{category_summary.get('completed_count', 0)}/{len(category_questions)}",
        )

        st.caption(
            "5 매우 우수 · 4 양호 · 3 중립/추가 확인 · "
            "2 주의 · 1 중대한 우려 · 0 치명적 문제 · N/A 비적용"
        )

        for question in category_questions:
            qno = int(question["question_no"])
            current = answer_map.get(qno)
            current_choice = answer_to_score_choice(current)
            status_text = current.get("status") if current else "UNKNOWN"

            with st.expander(
                f"Q{qno} · [{question['source_grade']}] · "
                f"{float(question['weight_pct']):.3f}% · {status_text}"
            ):
                st.markdown(f"**{question['question']}**")

                with st.form(f"manual_q_{analysis_id}_{qno}"):
                    score_choice = st.radio(
                        "평가",
                        SCORE_OPTIONS,
                        index=SCORE_OPTIONS.index(current_choice),
                        horizontal=True,
                    )
                    note = st.text_area(
                        "메모",
                        value=current.get("note") if current and current.get("note") else "",
                    )
                    evidence = st.text_input(
                        "근거 자료",
                        value=current.get("evidence") if current and current.get("evidence") else "",
                    )
                    evidence_url = st.text_input(
                        "근거 URL",
                        value=current.get("evidence_url") if current and current.get("evidence_url") else "",
                    )
                    save_clicked = st.form_submit_button(
                        "이 문항 저장",
                        type="primary",
                        use_container_width=True,
                    )

                if save_clicked:
                    score, status = score_to_status(score_choice)
                    save_answer(
                        analysis_id,
                        qno,
                        score,
                        status,
                        note,
                        evidence,
                        evidence_url,
                    )

                    refreshed = calculate_summary(
                        questions,
                        get_answers(analysis_id),
                    )
                    if refreshed["overall_score"] is not None:
                        update_analysis(
                            analysis_id,
                            total_score=refreshed["overall_score"],
                        )
                    st.rerun()


# =========================================================
# Manual result
# =========================================================
with tab_result:
    st.subheader("213문항 수동 분석 결과")
    analyses = get_analyses()

    if not analyses:
        st.info("아직 생성된 분석이 없습니다.")
    else:
        selected_analysis = analysis_selector(
            analyses,
            key="manual_result_selector",
        )
        analysis_id = selected_analysis["id"]
        company = selected_analysis.get("companies") or {}

        summary = calculate_summary(
            questions,
            get_answers(analysis_id),
        )

        score = summary["overall_score"]
        suggested = suggested_decision(summary)

        st.markdown(
            f"### {company.get('company_name', '-')} ({company.get('ticker', '-')})"
        )
        st.caption(
            f"Version {selected_analysis.get('version')} · "
            f"분석일 {selected_analysis.get('analysis_date')}"
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("종합점수", f"{score:.1f}" if score is not None else "-")
        c2.metric("진행률", f"{summary['completion_pct']:.1f}%")
        c3.metric("FAIL", summary["fail_count"])
        c4.metric("UNKNOWN", summary["unknown_count"])

        if summary["core_zero_items"]:
            st.error("핵심 3영역에 0점 문항이 존재합니다.")

        st.info(f"자동 참고판정: **{suggested}**")

        st.subheader("카테고리별 점수")
        st.dataframe(
            [
                {
                    "Category": c["category"],
                    "가중치": f"{float(c['category_weight_pct']):.1f}%",
                    "점수": (
                        round(
                            summary["category_data"].get(c["category"], {}).get("score"),
                            1,
                        )
                        if summary["category_data"].get(c["category"], {}).get("score") is not None
                        else None
                    ),
                    "완료": (
                        f"{summary['category_data'].get(c['category'], {}).get('completed_count', 0)}"
                        f"/{summary['category_data'].get(c['category'], {}).get('total_count', 0)}"
                    ),
                }
                for c in categories
            ],
            use_container_width=True,
            hide_index=True,
        )

        decision_options = [
            "GO",
            "WAIT",
            "NO-GO",
            "REVIEW REQUIRED",
        ]
        current_decision = selected_analysis.get("decision") or suggested
        if current_decision not in decision_options:
            current_decision = "REVIEW REQUIRED"

        with st.form(f"complete_{analysis_id}"):
            final_decision = st.selectbox(
                "최종 Decision",
                decision_options,
                index=decision_options.index(current_decision),
            )
            final_notes = st.text_area(
                "최종 메모",
                value=selected_analysis.get("notes") or "",
            )
            complete_clicked = st.form_submit_button(
                "분석 완료로 저장",
                type="primary",
                use_container_width=True,
            )

        if complete_clicked:
            update_analysis(
                analysis_id,
                total_score=score if score is not None else 0,
                status="COMPLETED",
                decision=final_decision,
                notes=final_notes,
            )
            st.rerun()


# =========================================================
# Auto Pabrai Score
# =========================================================
with tab_auto_scores:
    st.subheader("Pabrai Score (자동 1차)")

    st.warning(
        "이 점수는 213개 질문을 근거 없이 자동으로 전부 채운 값이 아닙니다. "
        "재무/시장 데이터로 측정 가능한 영역만 프록시로 평가하며 Coverage를 함께 확인해야 합니다."
    )

    ranked = [
        r for r in auto_scores
        if r.get("total_score") is not None
    ]
    ranked.sort(
        key=lambda x: float(x.get("total_score") or -1),
        reverse=True,
    )

    score_search = st.text_input(
        "Ticker 상세 조회",
        placeholder="AAPL 또는 005930",
        key="auto_score_search",
    ).strip().upper()

    if score_search:
        selected = None
        for row in auto_scores:
            company = row.get("companies") or {}
            if str(company.get("ticker", "")).upper() == score_search:
                selected = row
                break

        if not selected:
            st.info("아직 해당 기업의 자동 점수가 없습니다.")
        else:
            company = selected.get("companies") or {}
            c1, c2, c3 = st.columns(3)
            c1.metric("기업", f"{company.get('company_name')} ({company.get('ticker')})")
            c2.metric("Auto Pabrai Score", selected.get("total_score"))
            c3.metric("Coverage", f"{float(selected.get('coverage_pct') or 0):.1f}%")

            details = selected.get("details") or {}
            category_scores = details.get("category_scores") or {}
            factors = details.get("factors") or []

            if category_scores:
                st.subheader("카테고리별 Auto Score")
                st.dataframe(
                    [{"Category": k, "Score": v} for k, v in category_scores.items()],
                    use_container_width=True,
                    hide_index=True,
                )

            if factors:
                st.subheader("자동 Factor")
                st.dataframe(
                    factors,
                    use_container_width=True,
                    hide_index=True,
                )

    st.divider()
    st.subheader("전체 순위")
    st.dataframe(
        [
            {
                "Rank": i,
                "Ticker": (r.get("companies") or {}).get("ticker"),
                "기업명": (r.get("companies") or {}).get("company_name"),
                "Score": r.get("total_score"),
                "Coverage": f"{float(r.get('coverage_pct') or 0):.1f}%",
                "Status": r.get("status"),
                "기준일": r.get("as_of"),
            }
            for i, r in enumerate(ranked, start=1)
        ],
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# Framework foundation
# =========================================================
with tab_frameworks:
    st.subheader("다중 투자철학 Framework")

    st.write(
        "Pabrai 외에도 Buffett-style, Munger-style 분석을 동일한 DB 구조에서 "
        "확장할 수 있도록 Framework/Dimension을 분리했습니다."
    )

    for framework in frameworks:
        with st.expander(
            f"{framework['name']} · {framework['framework_type']}",
            expanded=framework["id"] == "PABRAI_AUTO",
        ):
            st.write(framework.get("description") or "")

            dims = get_framework_dimensions(framework["id"])
            st.dataframe(
                [
                    {
                        "Dimension": d["dimension_name"],
                        "Weight": f"{float(d['weight_pct']):.1f}%",
                        "설명": d.get("description"),
                    }
                    for d in dims
                ],
                use_container_width=True,
                hide_index=True,
            )

            if framework["id"] != "PABRAI_AUTO":
                st.info(
                    "기반 구조가 준비되어 있습니다. 다음 단계에서 각 Dimension에 "
                    "정량 Factor, 정성 체크리스트, 근거자료 및 AI 보조평가를 연결할 수 있습니다."
                )

st.divider()
st.caption(
    "Pabrai-style 213 Checklist · Auto Score + Manual Deep Dive · "
    "투자 의사결정 보조용 분석 도구"
)
