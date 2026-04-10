import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, unquote, urlparse
import pandas as pd
import time
import json
import re
from datetime import datetime, timedelta

# ─── 페이지 설정 ───
st.set_page_config(page_title="네이버 블로그 검사기", page_icon="🔍", layout="wide")
st.title("네이버 블로그 검사기")

# ─── 유틸 함수 ───

def extract_blog_id(url):
    """블로그 URL 또는 글 URL에서 블로그 ID 추출"""
    url = url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if parts:
        return parts[0]
    return None


def parse_naver_date(date_str):
    """네이버 날짜 문자열을 datetime으로 변환"""
    date_str = date_str.strip()
    now = datetime.now()

    # "N분 전", "N시간 전", "N일 전"
    m = re.match(r"(\d+)분 전", date_str)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.match(r"(\d+)시간 전", date_str)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.match(r"(\d+)일 전", date_str)
    if m:
        return now - timedelta(days=int(m.group(1)))

    # "2026. 4. 9." 형식
    m = re.match(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", date_str)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    return None


def crawl_blog_posts(blog_id, progress_callback=None):
    """블로그의 전체 글 목록 크롤링"""
    posts = []
    page = 1
    count_per_page = 30

    while True:
        url = (
            f"https://blog.naver.com/PostTitleListAsync.naver"
            f"?blogId={blog_id}&currentPage={page}&countPerPage={count_per_page}"
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://blog.naver.com/{blog_id}",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            text = resp.text

            # regex로 추출 (JSON 파싱 대신 — invalid escape 방지)
            titles = re.findall(r'"title":"([^"]*)"', text)
            dates = re.findall(r'"addDate":"([^"]*)"', text)
            lognos = re.findall(r'"logNo":"([^"]*)"', text)

            if not titles:
                break

            for t, d, l in zip(titles, dates, lognos):
                # URL 디코딩 + HTML 태그 제거
                title = unquote(t.replace("+", " "))
                title = re.sub(r"<[^>]+>", "", title)

                parsed_date = parse_naver_date(d)
                date_str = parsed_date.strftime("%Y-%m-%d") if parsed_date else d
                post_url = f"https://blog.naver.com/{blog_id}/{l}"

                posts.append({
                    "제목": title,
                    "작성일": date_str,
                    "URL": post_url,
                })

            if progress_callback:
                progress_callback(len(posts))

            if len(titles) < count_per_page:
                break

            page += 1
            time.sleep(0.5)

        except Exception as e:
            st.warning(f"크롤링 오류 (페이지 {page}): {e}")
            break

    return posts


def check_exposure(title, blog_id):
    """네이버 검색에서 해당 블로그 글이 노출되는지 확인"""
    encoded_query = quote(f'"{title}"')
    search_url = (
        f"https://search.naver.com/search.naver"
        f"?ssc=tab.nx.all&where=nexearch&query={encoded_query}&sm=tab_dgs&qdt=1"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    try:
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()
        return blog_id in resp.text
    except Exception:
        return False


def analyze_frequency(df):
    """빈도 + 노출률 연계 분석"""
    df["작성일_dt"] = pd.to_datetime(df["작성일"], errors="coerce")
    df = df.dropna(subset=["작성일_dt"])

    # 주간 그룹
    df["주차"] = df["작성일_dt"].dt.to_period("W").apply(lambda r: r.start_time)
    weekly = df.groupby("주차").agg(
        포스팅수=("제목", "count"),
        노출=("노출여부", lambda x: (x == "노출").sum()),
        미노출=("노출여부", lambda x: (x == "미노출").sum()),
    ).reset_index()
    weekly["노출률"] = (weekly["노출"] / weekly["포스팅수"] * 100).round(1)

    return weekly


# ─── 사이드바: 입력 ───

st.sidebar.header("블로그 주소 입력")
urls_input = st.sidebar.text_area(
    "한 줄에 하나씩 입력 (글 주소도 OK)",
    placeholder="https://blog.naver.com/blogid1\nhttps://blog.naver.com/blogid2/12345678",
    height=150,
)
period_option = st.sidebar.selectbox(
    "검사 기간",
    ["최근 15일", "최근 1달", "최근 3달", "전체"],
    index=1,
)
start_button = st.sidebar.button("🔍 검사 시작", use_container_width=True)

# ─── 메인 로직 ───

if start_button:
    if not urls_input.strip():
        st.error("블로그 주소를 입력해주세요.")
    else:
        # URL 파싱 → 블로그 ID 추출
        lines = [l.strip() for l in urls_input.strip().split("\n") if l.strip()]
        st.write(f"입력된 줄 수: {len(lines)}")
        blog_ids = []
        for line in lines:
            bid = extract_blog_id(line)
            st.write(f"  {line} → 블로그ID: {bid}")
            if bid and bid not in blog_ids:
                blog_ids.append(bid)

        if not blog_ids:
            st.error("유효한 블로그 주소를 입력해주세요.")
        else:
            all_results = {}

        for blog_id in blog_ids:
            st.markdown(f"---")
            st.subheader(f"📋 {blog_id}")

            # STEP 1: 크롤링
            status_container = st.container()
            status_text = status_container.empty()
            status_text.info(f"🔄 **{blog_id}** 글 목록 크롤링 중...")

            with st.spinner(f"{blog_id} 크롤링 중..."):
                posts = crawl_blog_posts(
                    blog_id,
                    progress_callback=lambda n: status_text.info(f"🔄 **{blog_id}** 크롤링 중... 수집된 글: **{n}개**"),
                )

            if not posts:
                status_text.error(f"❌ {blog_id}: 글을 찾을 수 없습니다.")
                continue

            status_text.success(f"✅ **{blog_id}** 전체 **{len(posts)}개** 글 수집 완료")

            # 기간 필터링
            period_days = {"최근 15일": 15, "최근 1달": 30, "최근 3달": 90, "전체": None}
            days = period_days[period_option]
            if days is not None:
                cutoff = datetime.now() - timedelta(days=days)
                filtered = []
                for p in posts:
                    dt = pd.to_datetime(p["작성일"], errors="coerce")
                    if pd.notna(dt) and dt >= cutoff:
                        filtered.append(p)
                posts = filtered

            if not posts:
                status_text.warning(f"⚠️ {blog_id}: {period_option} 기간에 해당하는 글이 없습니다.")
                continue

            # STEP 2: 노출 검사
            exposure_status = st.empty()
            progress_bar = st.progress(0)
            for i, post in enumerate(posts):
                exposure_status.info(f"🔍 노출 검사 중... ({i+1}/{len(posts)}) - {post['제목'][:30]}...")
                exposed = check_exposure(post["제목"], blog_id)
                post["노출여부"] = "노출" if exposed else "미노출"
                post["블로그ID"] = blog_id
                encoded_query = quote(f'"{post["제목"]}"')
                post["검색확인"] = f"https://search.naver.com/search.naver?ssc=tab.nx.all&where=nexearch&query={encoded_query}&sm=tab_dgs&qdt=1"
                progress_bar.progress((i + 1) / len(posts))
                time.sleep(1.5)  # 차단 방지 딜레이

            exposure_status.success(f"✅ **{blog_id}**: **{len(posts)}개** 글 노출 검사 완료!")
            progress_bar.empty()

            all_results[blog_id] = posts

        # ─── 결과 저장 (session_state) ───
        st.session_state["results"] = all_results


# ─── 결과 표시 ───

if "results" in st.session_state and st.session_state["results"]:
    results = st.session_state["results"]
    blog_ids = list(results.keys())

    # 블로그 선택 탭
    tabs = st.tabs(blog_ids)

    for tab, blog_id in zip(tabs, blog_ids):
        with tab:
            df = pd.DataFrame(results[blog_id])
            df["작성일_dt"] = pd.to_datetime(df["작성일"], errors="coerce")

            total = len(df)
            exposed = (df["노출여부"] == "노출").sum()
            not_exposed = (df["노출여부"] == "미노출").sum()
            exposure_rate = (exposed / total * 100) if total > 0 else 0

            # ── 요약 ──
            st.markdown("### 요약")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("총 글 수", f"{total}개")
            col2.metric("노출", f"{exposed}개")
            col3.metric("미노출", f"{not_exposed}개")
            col4.metric("노출률", f"{exposure_rate:.1f}%")

            # 빈도 계산
            if not df["작성일_dt"].isna().all():
                date_range = (df["작성일_dt"].max() - df["작성일_dt"].min()).days
                if date_range > 0:
                    weeks = date_range / 7
                    months = date_range / 30
                    daily_avg = total / date_range if date_range > 0 else 0
                    weekly_avg = total / weeks if weeks > 0 else 0
                    monthly_avg = total / months if months > 0 else 0

                    recent_30 = df[df["작성일_dt"] >= (datetime.now() - timedelta(days=30))]

                    col5, col6, col7, col8 = st.columns(4)
                    col5.metric("일당 평균", f"{daily_avg:.1f}회")
                    col6.metric("주당 평균", f"{weekly_avg:.1f}회")
                    col7.metric("월당 평균", f"{monthly_avg:.1f}회")
                    col8.metric("최근 30일", f"{len(recent_30)}개")

            # ── 빈도-노출률 상관관계 ──
            st.markdown("### 빈도-노출률 상관관계")
            weekly = analyze_frequency(df)

            if not weekly.empty:
                # 주간별 테이블
                weekly_display = weekly.copy()
                weekly_display["주차"] = weekly_display["주차"].dt.strftime("%Y-%m-%d")
                weekly_display.columns = ["주간 시작", "포스팅 수", "노출", "미노출", "노출률(%)"]
                st.dataframe(weekly_display, use_container_width=True, hide_index=True)

                # 산점도: 빈도 vs 노출률
                st.markdown("#### 주간 포스팅 빈도 vs 노출률")
                chart_data = weekly[["포스팅수", "노출률"]].rename(
                    columns={"포스팅수": "주간 포스팅 수", "노출률": "노출률(%)"}
                )
                st.scatter_chart(chart_data, x="주간 포스팅 수", y="노출률(%)")

                # 구간별 요약
                st.markdown("#### 빈도 구간별 평균 노출률")
                bins = [0, 2, 4, 6, float("inf")]
                labels = ["주 1~2회", "주 3~4회", "주 5~6회", "주 7회+"]
                weekly["구간"] = pd.cut(weekly["포스팅수"], bins=bins, labels=labels, right=True)
                segment = weekly.groupby("구간", observed=True).agg(
                    주수=("포스팅수", "count"),
                    평균_포스팅수=("포스팅수", "mean"),
                    평균_노출률=("노출률", "mean"),
                ).reset_index()
                segment["평균_포스팅수"] = segment["평균_포스팅수"].round(1)
                segment["평균_노출률"] = segment["평균_노출률"].round(1)
                segment.columns = ["구간", "해당 주 수", "평균 포스팅 수", "평균 노출률(%)"]
                st.dataframe(segment, use_container_width=True, hide_index=True)

            # ── 월별 포스팅 차트 ──
            st.markdown("### 월별 포스팅 수")
            if not df["작성일_dt"].isna().all():
                monthly = df.set_index("작성일_dt").resample("M").size().reset_index(name="포스팅 수")
                monthly["월"] = monthly["작성일_dt"].dt.strftime("%Y-%m")
                st.bar_chart(monthly.set_index("월")["포스팅 수"])

            # ── 글 목록 ──
            st.markdown("### 글 목록")
            filter_option = st.radio(
                "필터",
                ["전체", "노출만", "미노출만"],
                horizontal=True,
                key=f"filter_{blog_id}",
            )

            display_df = df[["제목", "작성일", "노출여부", "URL", "검색확인"]].copy()
            if filter_option == "노출만":
                display_df = display_df[display_df["노출여부"] == "노출"]
            elif filter_option == "미노출만":
                display_df = display_df[display_df["노출여부"] == "미노출"]

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "URL": st.column_config.LinkColumn("URL"),
                    "검색확인": st.column_config.LinkColumn("검색확인"),
                },
            )

            # ── CSV 다운로드 ──
            csv = display_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 CSV 다운로드",
                data=csv,
                file_name=f"blog_check_{blog_id}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                key=f"csv_{blog_id}",
            )

    # ── 전체 CSV 다운로드 ──
    if len(blog_ids) > 1:
        st.markdown("---")
        all_df = pd.concat([pd.DataFrame(results[bid]) for bid in blog_ids])
        all_csv = all_df[["블로그ID", "제목", "작성일", "노출여부", "URL"]].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 전체 블로그 CSV 다운로드",
            data=all_csv,
            file_name=f"blog_check_all_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )
