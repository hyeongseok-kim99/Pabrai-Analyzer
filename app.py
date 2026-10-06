import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Pabrai Analyzer",
    page_icon="📊",
    layout="wide",
)

st.title("Pabrai 213 Analyzer")
st.caption("기업을 213개 투자 체크리스트로 평가하는 분석 도구")

try:
    supabase = create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SECRET_KEY"],
    )

    questions_result = (
        supabase
        .table("questions")
        .select("question_no", count="exact")
        .execute()
    )

    categories_result = (
        supabase
        .table("categories")
        .select("category", count="exact")
        .execute()
    )

    question_count = questions_result.count or 0
    category_count = categories_result.count or 0

    st.success("Supabase 연결에 성공했습니다.")

    col1, col2 = st.columns(2)

    with col1:
        st.metric("체크리스트 문항", question_count)

    with col2:
        st.metric("카테고리", category_count)

    if question_count == 213 and category_count == 10:
        st.info("213개 질문과 10개 카테고리가 정상적으로 연결되어 있습니다.")
    else:
        st.warning(
            f"예상값과 다릅니다. 질문 {question_count}/213, "
            f"카테고리 {category_count}/10"
        )

except Exception as e:
    st.error("Supabase 연결에 실패했습니다.")
    st.code(str(e))
