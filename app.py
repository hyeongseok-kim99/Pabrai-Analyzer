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
# Helper functions
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


def get_analyses(limit=50):
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
            created_at,
            companies (
                ticker,
                company_name,
                market
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


# =========================================================
# Header
# =========================================================
st.title("Pabrai 213 Analyzer")
st.caption("213개 투자 체크리스트를 이용한 기업 분석 시스템")


# =========================================================
# DB connection check
# =========================================================
try:
    question_result = (
        supabase
        .table("questions")
        .select("question_no", count="exact")
        .execute()
    )

    question_count = question_result.count or 0

except Exception as e:
    st.error("Supabase 연결에 실패했습니다.")
    st.code(str(e))
    st.stop()


# =========================================================
# Main navigation
# =========================================================
tab_dashboard, tab_companies, tab_analysis = st.tabs(
    [
        "📊 Dashboard",
        "🏢 기업 관리",
        "✅ 새 분석",
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

        with col1:
            st.metric(
                "체크리스트",
                f"{question_count}개",
            )

        with col2:
            st.metric(
                "등록 기업",
                len(companies),
            )

        with col3:
            st.metric(
                "완료 분석",
                completed_count,
            )

        with col4:
            st.metric(
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

            for analysis in analyses[:20]:

                company = (
                    analysis.get("companies")
                    or {}
                )

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
            st.error(
                "Ticker를 입력해주세요."
            )

        elif not company_name_clean:
            st.error(
                "기업명을 입력해주세요."
            )

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

                supabase.table(
                    "companies"
                ).insert(
                    data
                ).execute()

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
                        "같은 Ticker와 Market을 가진 기업이 "
                        "이미 등록되어 있습니다."
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
# New Analysis
# =========================================================
with tab_analysis:

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

            with col1:
                st.metric(
                    "기업",
                    selected_company[
                        "company_name"
                    ],
                )

            with col2:
                st.metric(
                    "Ticker",
                    selected_company[
                        "ticker"
                    ],
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

                    st.success(
                        "새 분석이 생성되었습니다."
                    )

                    st.write(
                        f"Version: "
                        f"**{new_analysis['version']}**"
                    )

                    st.write(
                        "Status: **DRAFT**"
                    )

                    st.session_state[
                        "current_analysis_id"
                    ] = new_analysis["id"]

                    st.session_state[
                        "current_company_id"
                    ] = company_id

                    st.session_state[
                        "current_company_name"
                    ] = selected_company[
                        "company_name"
                    ]

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
# Footer
# =========================================================
st.divider()

st.caption(
    "Pabrai-style 213 Checklist · "
    "Leverage → Moat → Management & Ownership"
)
