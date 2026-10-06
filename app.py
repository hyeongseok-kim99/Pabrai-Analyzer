import streamlit as st
from supabase import create_client
from datetime import date

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
# Data helpers
# =========================================================
def get_companies():
    result = (
        supabase
        .table("companies")
        .select("*")
        .order("company_name")
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


def get_analyses(limit=200):
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
    next_version = get_next_version(company_id)

    result = (
        supabase
        .table("analyses")
        .insert(
            {
                "company_id": company_id,
                "version": next_version,
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
# Scoring helpers
# =========================================================
def score_to_status(score_choice):
    if score_choice == "미평가":
        return None, "UNKNOWN"

    if score_choice == "N/A":
        return None, "N/A"

    score = int(score_choice)

    if score >= 4:
        status = "PASS"
    elif score >= 2:
        status = "WARNING"
    else:
        status = "FAIL"

    return score, status


def answer_to_score_choice(answer):
    if not answer:
        return "미평가"

    status = answer.get("status")

    if status == "N/A":
        return "N/A"

    score = answer.get("score")

    if score is None:
        return "미평가"

    return str(score)


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
    numeric_count = 0
    na_count = 0
    unknown_count = 0
    fail_count = 0
    warning_count = 0
    pass_count = 0
    core_zero_items = []

    for question in questions:
        category = question["category"]
        weight = float(question["weight_pct"])
        qno = int(question["question_no"])

        if category not in category_data:
            category_data[category] = {
                "weighted": 0.0,
                "rated_weight": 0.0,
                "numeric_count": 0,
                "completed_count": 0,
                "total_count": 0,
            }

        category_data[category]["total_count"] += 1

        answer = answer_map.get(qno)

        if not answer or answer.get("status") == "UNKNOWN":
            unknown_count += 1
            continue

        completed_count += 1
        category_data[category]["completed_count"] += 1

        if answer.get("status") == "N/A":
            na_count += 1
            continue

        score = answer.get("score")

        if score is None:
            unknown_count += 1
            continue

        score = int(score)
        numeric_count += 1
        category_data[category]["numeric_count"] += 1

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

        if (
            score == 0
            and category in CORE_CATEGORIES
        ):
            core_zero_items.append(
                {
                    "question_no": qno,
                    "category": category,
                    "question": question["question"],
                }
            )

    overall_score = None

    if total_rated_weight > 0:
        overall_score = (
            total_weighted
            / total_rated_weight
            * 100.0
        )

    for category, data in category_data.items():
        if data["rated_weight"] > 0:
            data["score"] = (
                data["weighted"]
                / data["rated_weight"]
                * 100.0
            )
        else:
            data["score"] = None

    total_questions = len(questions)

    completion_pct = (
        completed_count / total_questions * 100.0
        if total_questions
        else 0.0
    )

    return {
        "overall_score": overall_score,
        "category_data": category_data,
        "completed_count": completed_count,
        "numeric_count": numeric_count,
        "na_count": na_count,
        "unknown_count": max(
            0,
            total_questions - completed_count,
        ),
        "pass_count": pass_count,
        "warning_count": warning_count,
        "fail_count": fail_count,
        "core_zero_items": core_zero_items,
        "completion_pct": completion_pct,
        "rated_weight_pct": total_rated_weight,
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


# =========================================================
# UI helpers
# =========================================================
def analysis_label(analysis):
    company = analysis.get("companies") or {}
    company_name = company.get("company_name", "-")
    ticker = company.get("ticker", "-")
    version = analysis.get("version", "-")
    status = analysis.get("status", "-")

    return (
        f"{company_name} ({ticker}) · "
        f"V{version} · {status}"
    )


def analysis_selector(
    analyses,
    key,
    label="분석 선택",
):
    if not analyses:
        return None

    analysis_by_label = {
        analysis_label(item): item
        for item in analyses
    }

    labels = list(analysis_by_label.keys())

    preferred_id = st.session_state.get(
        "current_analysis_id"
    )

    default_index = 0

    if preferred_id:
        for idx, label_text in enumerate(labels):
            if (
                analysis_by_label[label_text]["id"]
                == preferred_id
            ):
                default_index = idx
                break

    selected_label = st.selectbox(
        label,
        labels,
        index=default_index,
        key=key,
    )

    return analysis_by_label[selected_label]


# =========================================================
# Header
# =========================================================
st.title("Pabrai 213 Analyzer")
st.caption(
    "Pabrai-style 213개 체크리스트를 이용한 기업 분석 시스템"
)

# =========================================================
# DB connection check
# =========================================================
try:
    questions = get_questions()
    categories = get_categories()
    question_count = len(questions)

except Exception as e:
    st.error("Supabase 연결에 실패했습니다.")
    st.code(str(e))
    st.stop()


# =========================================================
# Navigation
# =========================================================
(
    tab_dashboard,
    tab_companies,
    tab_new_analysis,
    tab_checklist,
    tab_result,
) = st.tabs(
    [
        "📊 Dashboard",
        "🏢 기업 관리",
        "➕ 새 분석",
        "✅ Checklist",
        "📈 결과",
    ]
)


# =========================================================
# Dashboard
# =========================================================
with tab_dashboard:
    try:
        companies = get_companies()
        analyses = get_analyses()

        completed_count = sum(
            1
            for item in analyses
            if item.get("status") == "COMPLETED"
        )

        draft_count = sum(
            1
            for item in analyses
            if item.get("status") == "DRAFT"
        )

        col1, col2, col3, col4 = st.columns(4)

        col1.metric(
            "체크리스트",
            f"{question_count}개",
        )
        col2.metric(
            "등록 기업",
            len(companies),
        )
        col3.metric(
            "완료 분석",
            completed_count,
        )
        col4.metric(
            "진행 중",
            draft_count,
        )

        st.divider()
        st.subheader("최근 분석")

        if not analyses:
            st.info(
                "아직 생성된 기업 분석이 없습니다."
            )

        else:
            display_rows = []

            for analysis in analyses[:30]:
                company = analysis.get("companies") or {}

                display_rows.append(
                    {
                        "기업": company.get(
                            "company_name",
                            "-"
                        ),
                        "Ticker": company.get(
                            "ticker",
                            "-"
                        ),
                        "Version": analysis.get(
                            "version"
                        ),
                        "분석일": analysis.get(
                            "analysis_date"
                        ),
                        "상태": analysis.get(
                            "status"
                        ),
                        "점수": analysis.get(
                            "total_score"
                        ),
                        "결정": analysis.get(
                            "decision"
                        ),
                    }
                )

            st.dataframe(
                display_rows,
                use_container_width=True,
                hide_index=True,
            )

    except Exception as e:
        st.error(
            "Dashboard 데이터를 불러오는 중 오류가 발생했습니다."
        )
        st.code(str(e))


# =========================================================
# Companies
# =========================================================
with tab_companies:
    st.subheader("기업 추가")

    with st.form(
        "add_company_form",
        clear_on_submit=True,
    ):
        col1, col2 = st.columns(2)

        with col1:
            ticker = st.text_input(
                "Ticker *",
                placeholder="예: AAPL",
            )
            company_name = st.text_input(
                "기업명 *",
                placeholder="예: Apple Inc.",
            )

        with col2:
            market = st.text_input(
                "Market",
                placeholder="예: NASDAQ",
            )
            country = st.text_input(
                "Country",
                placeholder="예: USA",
            )

        submitted = st.form_submit_button(
            "기업 추가",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        ticker_clean = ticker.strip().upper()
        company_name_clean = company_name.strip()
        market_clean = market.strip().upper()
        country_clean = country.strip()

        if not ticker_clean:
            st.error("Ticker를 입력해주세요.")

        elif not company_name_clean:
            st.error("기업명을 입력해주세요.")

        else:
            try:
                data = {
                    "ticker": ticker_clean,
                    "company_name": company_name_clean,
                    "market": (
                        market_clean
                        if market_clean
                        else None
                    ),
                    "country": (
                        country_clean
                        if country_clean
                        else None
                    ),
                }

                (
                    supabase
                    .table("companies")
                    .insert(data)
                    .execute()
                )

                st.success(
                    f"{company_name_clean} "
                    f"({ticker_clean})을 추가했습니다."
                )

                st.rerun()

            except Exception as e:
                error_text = str(e)

                if (
                    "duplicate" in error_text.lower()
                    or "unique" in error_text.lower()
                ):
                    st.warning(
                        "같은 Ticker와 Market을 가진 "
                        "기업이 이미 등록되어 있습니다."
                    )
                else:
                    st.error(
                        "기업 추가 중 오류가 발생했습니다."
                    )
                    st.code(error_text)

    st.divider()
    st.subheader("등록된 기업")

    try:
        companies = get_companies()

        if not companies:
            st.info(
                "아직 등록된 기업이 없습니다."
            )

        else:
            company_rows = []

            for company in companies:
                company_rows.append(
                    {
                        "Ticker": company.get(
                            "ticker"
                        ),
                        "기업명": company.get(
                            "company_name"
                        ),
                        "Market": company.get(
                            "market"
                        ),
                        "Country": company.get(
                            "country"
                        ),
                    }
                )

            st.dataframe(
                company_rows,
                use_container_width=True,
                hide_index=True,
            )

    except Exception as e:
        st.error(
            "기업 목록을 불러오지 못했습니다."
        )
        st.code(str(e))


# =========================================================
# New analysis
# =========================================================
with tab_new_analysis:
    st.subheader("새 기업 분석")

    try:
        companies = get_companies()

        if not companies:
            st.info(
                "먼저 '기업 관리' 탭에서 "
                "분석할 기업을 추가해주세요."
            )

        else:
            company_map = {}

            for company in companies:
                ticker_text = company.get(
                    "ticker",
                    ""
                )
                company_name_text = company.get(
                    "company_name",
                    ""
                )
                market_text = company.get(
                    "market"
                )

                if market_text:
                    label = (
                        f"{company_name_text} "
                        f"({ticker_text} · {market_text})"
                    )
                else:
                    label = (
                        f"{company_name_text} "
                        f"({ticker_text})"
                    )

                company_map[label] = company

            selected_label = st.selectbox(
                "분석할 기업",
                list(company_map.keys()),
            )

            selected_company = company_map[
                selected_label
            ]
            company_id = selected_company["id"]
            next_version = get_next_version(
                company_id
            )

            st.info(
                f"새 분석은 Version "
                f"{next_version}으로 생성됩니다."
            )

            col1, col2 = st.columns(2)

            col1.metric(
                "기업",
                selected_company["company_name"],
            )
            col2.metric(
                "Ticker",
                selected_company["ticker"],
            )

            st.write(
                f"분석일: **{date.today()}**"
            )

            if st.button(
                "새 분석 시작",
                type="primary",
                use_container_width=True,
            ):
                try:
                    new_analysis = create_analysis(
                        company_id
                    )

                    st.session_state[
                        "current_analysis_id"
                    ] = new_analysis["id"]

                    st.success(
                        "새 분석이 생성되었습니다. "
                        "Checklist 탭에서 평가를 시작하세요."
                    )

                    st.write(
                        f"Version: "
                        f"**{new_analysis['version']}**"
                    )
                    st.write(
                        "Status: **DRAFT**"
                    )

                except Exception as e:
                    st.error(
                        "새 분석 생성 중 "
                        "오류가 발생했습니다."
                    )
                    st.code(str(e))

    except Exception as e:
        st.error(
            "기업 데이터를 불러오는 중 "
            "오류가 발생했습니다."
        )
        st.code(str(e))


# =========================================================
# Checklist
# =========================================================
with tab_checklist:
    st.subheader("213 Checklist")

    try:
        analyses = get_analyses()

        if not analyses:
            st.info(
                "먼저 '새 분석' 탭에서 "
                "분석을 생성해주세요."
            )

        else:
            selected_analysis = analysis_selector(
                analyses,
                key="checklist_analysis_selector",
            )

            if selected_analysis:
                st.session_state[
                    "current_analysis_id"
                ] = selected_analysis["id"]

                analysis_id = selected_analysis["id"]
                company = (
                    selected_analysis.get(
                        "companies"
                    )
                    or {}
                )

                answers = get_answers(analysis_id)
                answer_map = build_answer_map(
                    answers
                )
                summary = calculate_summary(
                    questions,
                    answers,
                )

                col1, col2, col3, col4 = st.columns(4)

                col1.metric(
                    "기업",
                    company.get(
                        "ticker",
                        "-"
                    ),
                )

                col2.metric(
                    "Version",
                    selected_analysis.get(
                        "version",
                        "-"
                    ),
                )

                col3.metric(
                    "평가 완료",
                    (
                        f"{summary['completed_count']}"
                        f"/{question_count}"
                    ),
                )

                col4.metric(
                    "현재 점수",
                    (
                        f"{summary['overall_score']:.1f}"
                        if summary["overall_score"]
                        is not None
                        else "-"
                    ),
                )

                st.progress(
                    min(
                        1.0,
                        summary["completion_pct"]
                        / 100.0,
                    ),
                    text=(
                        f"진행률 "
                        f"{summary['completion_pct']:.1f}%"
                    ),
                )

                if summary["core_zero_items"]:
                    st.error(
                        "핵심 영역에서 0점 항목이 "
                        f"{len(summary['core_zero_items'])}개 "
                        "있습니다."
                    )

                category_names = [
                    item["category"]
                    for item in categories
                ]

                selected_category = st.selectbox(
                    "Category",
                    category_names,
                    key="checklist_category",
                )

                category_questions = [
                    q
                    for q in questions
                    if q["category"]
                    == selected_category
                ]

                category_summary = summary[
                    "category_data"
                ].get(
                    selected_category,
                    {}
                )

                category_score = category_summary.get(
                    "score"
                )

                cat_col1, cat_col2, cat_col3 = st.columns(3)

                cat_col1.metric(
                    "카테고리 문항",
                    len(category_questions),
                )

                cat_col2.metric(
                    "카테고리 점수",
                    (
                        f"{category_score:.1f}"
                        if category_score
                        is not None
                        else "-"
                    ),
                )

                cat_col3.metric(
                    "완료",
                    (
                        f"{category_summary.get('completed_count', 0)}"
                        f"/{len(category_questions)}"
                    ),
                )

                st.caption(
                    "점수 기준: "
                    "5 매우 우수 · 4 양호 · "
                    "3 중립/추가 확인 · "
                    "2 주의 · 1 중대한 우려 · "
                    "0 치명적 문제 · N/A 비적용"
                )

                st.divider()

                for question in category_questions:
                    qno = int(
                        question["question_no"]
                    )
                    current = answer_map.get(qno)

                    current_choice = (
                        answer_to_score_choice(
                            current
                        )
                    )

                    status_text = (
                        current.get("status")
                        if current
                        else "UNKNOWN"
                    )

                    expander_title = (
                        f"Q{qno} · "
                        f"[{question['source_grade']}] · "
                        f"{float(question['weight_pct']):.3f}% · "
                        f"{status_text}"
                    )

                    with st.expander(
                        expander_title,
                        expanded=False,
                    ):
                        st.markdown(
                            f"**{question['question']}**"
                        )

                        with st.form(
                            f"answer_form_{analysis_id}_{qno}"
                        ):
                            score_choice = st.radio(
                                "평가",
                                SCORE_OPTIONS,
                                index=SCORE_OPTIONS.index(
                                    current_choice
                                ),
                                horizontal=True,
                                key=(
                                    f"score_"
                                    f"{analysis_id}_"
                                    f"{qno}"
                                ),
                            )

                            note = st.text_area(
                                "메모",
                                value=(
                                    current.get("note")
                                    if current
                                    and current.get("note")
                                    else ""
                                ),
                                placeholder=(
                                    "판단 근거, 위험요인, "
                                    "추가 확인사항 등을 기록"
                                ),
                                key=(
                                    f"note_"
                                    f"{analysis_id}_"
                                    f"{qno}"
                                ),
                            )

                            evidence = st.text_input(
                                "근거 자료",
                                value=(
                                    current.get(
                                        "evidence"
                                    )
                                    if current
                                    and current.get(
                                        "evidence"
                                    )
                                    else ""
                                ),
                                placeholder=(
                                    "예: 2026 10-K p.72, "
                                    "IR 자료 등"
                                ),
                                key=(
                                    f"evidence_"
                                    f"{analysis_id}_"
                                    f"{qno}"
                                ),
                            )

                            evidence_url = st.text_input(
                                "근거 URL",
                                value=(
                                    current.get(
                                        "evidence_url"
                                    )
                                    if current
                                    and current.get(
                                        "evidence_url"
                                    )
                                    else ""
                                ),
                                placeholder="https://...",
                                key=(
                                    f"url_"
                                    f"{analysis_id}_"
                                    f"{qno}"
                                ),
                            )

                            save_clicked = (
                                st.form_submit_button(
                                    "이 문항 저장",
                                    type="primary",
                                    use_container_width=True,
                                )
                            )

                        if save_clicked:
                            try:
                                score, status = (
                                    score_to_status(
                                        score_choice
                                    )
                                )

                                save_answer(
                                    analysis_id,
                                    qno,
                                    score,
                                    status,
                                    note,
                                    evidence,
                                    evidence_url,
                                )

                                refreshed_answers = (
                                    get_answers(
                                        analysis_id
                                    )
                                )

                                refreshed_summary = (
                                    calculate_summary(
                                        questions,
                                        refreshed_answers,
                                    )
                                )

                                if (
                                    refreshed_summary[
                                        "overall_score"
                                    ]
                                    is not None
                                ):
                                    update_analysis(
                                        analysis_id,
                                        total_score=(
                                            refreshed_summary[
                                                "overall_score"
                                            ]
                                        ),
                                    )

                                st.success(
                                    f"Q{qno} 저장 완료"
                                )
                                st.rerun()

                            except Exception as e:
                                st.error(
                                    "저장 중 오류가 "
                                    "발생했습니다."
                                )
                                st.code(str(e))

    except Exception as e:
        st.error(
            "Checklist를 불러오는 중 "
            "오류가 발생했습니다."
        )
        st.code(str(e))


# =========================================================
# Result
# =========================================================
with tab_result:
    st.subheader("분석 결과")

    try:
        analyses = get_analyses()

        if not analyses:
            st.info(
                "아직 생성된 분석이 없습니다."
            )

        else:
            selected_analysis = analysis_selector(
                analyses,
                key="result_analysis_selector",
            )

            analysis_id = selected_analysis["id"]
            company = (
                selected_analysis.get(
                    "companies"
                )
                or {}
            )

            answers = get_answers(
                analysis_id
            )

            summary = calculate_summary(
                questions,
                answers,
            )

            overall_score = summary[
                "overall_score"
            ]

            suggested = suggested_decision(
                summary
            )

            st.markdown(
                f"### {company.get('company_name', '-')} "
                f"({company.get('ticker', '-')})"
            )

            st.caption(
                f"Version {selected_analysis.get('version')} · "
                f"분석일 {selected_analysis.get('analysis_date')}"
            )

            col1, col2, col3, col4 = st.columns(4)

            col1.metric(
                "종합점수",
                (
                    f"{overall_score:.1f}"
                    if overall_score is not None
                    else "-"
                ),
            )

            col2.metric(
                "평가 진행률",
                f"{summary['completion_pct']:.1f}%",
            )

            col3.metric(
                "FAIL",
                summary["fail_count"],
            )

            col4.metric(
                "UNKNOWN",
                summary["unknown_count"],
            )

            st.progress(
                min(
                    1.0,
                    summary["completion_pct"]
                    / 100.0,
                ),
                text=(
                    f"{summary['completed_count']}"
                    f"/{question_count} 문항 완료"
                ),
            )

            if summary["core_zero_items"]:
                st.error(
                    "Leverage / Moat / "
                    "Management & Ownership에서 "
                    "0점 문항이 존재합니다. "
                    "종합점수와 별도로 반드시 검토하세요."
                )

                for item in summary[
                    "core_zero_items"
                ]:
                    st.write(
                        f"- Q{item['question_no']} "
                        f"[{item['category']}] "
                        f"{item['question']}"
                    )

            st.info(
                f"자동 참고판정: **{suggested}**  \n"
                "이 판정은 공식 Pabrai 기준이 아니라 "
                "현재 앱의 보조 규칙입니다."
            )

            st.divider()
            st.subheader("카테고리별 점수")

            category_rows = []

            for category in categories:
                category_name = category["category"]
                data = summary[
                    "category_data"
                ].get(
                    category_name,
                    {}
                )

                category_rows.append(
                    {
                        "Category": category_name,
                        "배정 가중치": (
                            f"{float(category['category_weight_pct']):.1f}%"
                        ),
                        "점수": (
                            round(
                                data["score"],
                                1,
                            )
                            if data.get("score")
                            is not None
                            else None
                        ),
                        "완료": (
                            f"{data.get('completed_count', 0)}"
                            f"/{data.get('total_count', 0)}"
                        ),
                    }
                )

            st.dataframe(
                category_rows,
                use_container_width=True,
                hide_index=True,
            )

            st.divider()
            st.subheader("분석 완료 / 최종 판단")

            current_decision = (
                selected_analysis.get(
                    "decision"
                )
                or suggested
            )

            decision_options = [
                "GO",
                "WAIT",
                "NO-GO",
                "REVIEW REQUIRED",
            ]

            if current_decision not in decision_options:
                current_decision = "REVIEW REQUIRED"

            with st.form(
                f"complete_analysis_{analysis_id}"
            ):
                final_decision = st.selectbox(
                    "최종 Decision",
                    decision_options,
                    index=decision_options.index(
                        current_decision
                    ),
                )

                final_notes = st.text_area(
                    "분석 요약 / 최종 메모",
                    value=(
                        selected_analysis.get(
                            "notes"
                        )
                        or ""
                    ),
                    placeholder=(
                        "핵심 투자 논리, 반대 논리, "
                        "확인해야 할 조건 등을 기록"
                    ),
                )

                complete_clicked = (
                    st.form_submit_button(
                        "분석 완료로 저장",
                        type="primary",
                        use_container_width=True,
                    )
                )

            if complete_clicked:
                if summary["unknown_count"] > 0:
                    st.warning(
                        f"아직 UNKNOWN 문항이 "
                        f"{summary['unknown_count']}개 있습니다. "
                        "그래도 완료 저장은 가능하지만 "
                        "추가 확인을 권장합니다."
                    )

                try:
                    update_analysis(
                        analysis_id,
                        total_score=(
                            overall_score
                            if overall_score
                            is not None
                            else 0
                        ),
                        status="COMPLETED",
                        decision=final_decision,
                        notes=final_notes,
                    )

                    st.success(
                        "분석 결과를 완료 상태로 "
                        "저장했습니다."
                    )
                    st.rerun()

                except Exception as e:
                    st.error(
                        "분석 완료 저장 중 "
                        "오류가 발생했습니다."
                    )
                    st.code(str(e))

    except Exception as e:
        st.error(
            "분석 결과를 불러오는 중 "
            "오류가 발생했습니다."
        )
        st.code(str(e))


# =========================================================
# Footer
# =========================================================
st.divider()

st.caption(
    "Pabrai-style 213 Checklist · "
    "Leverage → Moat → Management & Ownership · "
    "투자 의사결정을 보조하기 위한 분석 도구"
)
