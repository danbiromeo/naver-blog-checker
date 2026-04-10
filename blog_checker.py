import streamlit as st
import requests
from urllib.parse import quote, unquote, urlparse
import pandas as pd
import time
import re
from datetime import datetime, timedelta

# ─── 페이지 설정 ───
st.set_page_config(page_title="네이버 블로그 검사기", page_icon="🔍", layout="wide")
st.title("네이버 블로그 검사기")

# ─── session_state 초기화 ───
if "crawl_results" not in st.session_state:
    st.session_state["crawl_results"] = {}  # {blog_id: [posts]}
if "exposure_done" not in st.session_state:
    st.session_state["exposure_done"] = set()  # 노출 검사 완료된 blog_id

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

    m = re.match(r"(\d+)분 전", date_str)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.match(r"(\d+)시간 전", date_str)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.match(r"(\d+)일 전", date_str)
    if m:
        return now - timedelta(days=int(m.group(1)))

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

            titles = re.findall(r'"title":"([^"]*)"', text)
            dates = re.findall(r'"addDate":"([^"]*)"', text)
            lognos = re.findall(r'"logNo":"([^"]*)"', text)

            if not titles:
                break

            for t, d, l in zip(titles, dates, lognos):
                title = unquote(t.replace("+", " "))
                title = re.sub(r"<[^>]+>", "", title)

                parsed_date = parse_naver_date(d)
                date_str = parsed_date.strftime("%Y-%m-%d") if parsed_date else d
                post_url = f"https://blog.naver.com/{blog_id}/{l}"

                posts.append({
                    "제목": title,
                    "작성일": date_str,
                    "URL": post_url,
                    "노출여부": "-",
                    "검색확인": "",
                    "블로그ID": blog_id,
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


def filter_by_period(posts, period_option):
    """기간별 필터링"""
    period_days = {"최근 7일": 7, "최근 15일": 15, "최근 1달": 30, "최근 3달": 90, "전체": None}
    days = period_days[period_option]
    if days is None:
        return posts
    cutoff = datetime.now() - timedelta(days=days)
    filtered = []
    for p in posts:
        dt = pd.to_datetime(p["작성일"], errors="coerce")
        if pd.notna(dt) and dt >= cutoff:
            filtered.append(p)
    return filtered


def get_period_label(period_option):
    return {"최근 7일": "이번주", "최근 15일": "최근 15일", "최근 1달": "최근 1달", "최근 3달": "최근 3달", "전체": "전체"}[period_option]


# ─── 사이드바: 입력 ───

st.sidebar.header("블로그 주소 입력")
urls_input = st.sidebar.text_area(
    "한 줄에 하나씩 입력 (글 주소도 OK)",
    placeholder="https://blog.naver.com/blogid1\nhttps://blog.naver.com/blogid2/12345678",
    height=150,
)
period_option = st.sidebar.selectbox(
    "검사 기간",
    ["최근 7일", "최근 15일", "최근 1달", "최근 3달", "전체"],
    index=1,
)
freq_button = st.sidebar.button("📊 빈도 검사", use_container_width=True)

# ─── STEP 1: 빈도 검사 (크롤링만) ───

if freq_button:
    if not urls_input.strip():
        st.error("블로그 주소를 입력해주세요.")
    else:
        lines = [l.strip() for l in urls_input.strip().split("\n") if l.strip()]
        blog_ids = []
        for line in lines:
            bid = extract_blog_id(line)
            if bid and bid not in blog_ids:
                blog_ids.append(bid)

        if not blog_ids:
            st.error("유효한 블로그 주소를 입력해주세요.")
        else:
            # 기존 결과 초기화
            st.session_state["crawl_results"] = {}
            st.session_state["exposure_done"] = set()

            for blog_id in blog_ids:
                status_text = st.empty()
                status_text.info(f"🔄 **{blog_id}** 크롤링 중...")

                with st.spinner(f"{blog_id} 크롤링 중..."):
                    posts = crawl_blog_posts(
                        blog_id,
                        progress_callback=lambda n, bid=blog_id: status_text.info(f"🔄 **{bid}** 크롤링 중... 수집된 글: **{n}개**"),
                    )

                if not posts:
                    status_text.error(f"❌ {blog_id}: 글을 찾을 수 없습니다.")
                    continue

                total_posts = len(posts)
                filtered = filter_by_period(posts, period_option)
                label = get_period_label(period_option)

                if not filtered:
                    status_text.warning(f"⚠️ {blog_id}: 전체 {total_posts}개 중 {period_option} 기간에 해당하는 글이 없습니다.")
                    continue

                status_text.success(f"✅ **{blog_id}** — 전체 글 **{total_posts}개** / {label} 글 **{len(filtered)}개**")
                st.session_state["crawl_results"][blog_id] = filtered

            st.session_state["period_option"] = period_option
            st.rerun()


# ─── 결과 표시 ───

if st.session_state["crawl_results"]:
    results = st.session_state["crawl_results"]
    blog_ids = list(results.keys())
    period_label = get_period_label(st.session_state.get("period_option", "최근 15일"))

    # ── 블로그 비교 요약 테이블 ──
    st.markdown("### 블로그 비교 요약")
    summary_rows = []
    for bid in blog_ids:
        bdf = pd.DataFrame(results[bid])
        bdf["작성일_dt"] = pd.to_datetime(bdf["작성일"], errors="coerce")
        total = len(bdf)
        date_range = (bdf["작성일_dt"].max() - bdf["작성일_dt"].min()).days if not bdf["작성일_dt"].isna().all() else 0
        daily = total / date_range if date_range > 0 else 0
        weekly = daily * 7

        row = {
            "블로그ID": bid,
            f"{period_label} 글 수": total,
            "일당 빈도": round(daily, 1),
            "주당 빈도": round(weekly, 1),
        }

        if bid in st.session_state["exposure_done"]:
            exposed = sum(1 for p in results[bid] if p["노출여부"] == "노출")
            not_exposed = sum(1 for p in results[bid] if p["노출여부"] == "미노출")
            rate = (exposed / total * 100) if total > 0 else 0
            row["노출"] = exposed
            row["미노출"] = not_exposed
            row["노출률(%)"] = round(rate, 1)
        else:
            row["노출"] = "-"
            row["미노출"] = "-"
            row["노출률(%)"] = "-"

        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    # 전체 CSV 다운로드
    all_df = pd.concat([pd.DataFrame(results[bid]) for bid in blog_ids])
    csv_cols = ["블로그ID", "제목", "작성일", "노출여부", "URL"]
    if any(bid in st.session_state["exposure_done"] for bid in blog_ids):
        csv_cols.append("검색확인")
    all_csv = all_df[csv_cols].to_csv(index=False).encode("utf-8-sig")
    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        summary_csv = summary_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 비교 요약 CSV 다운로드",
            data=summary_csv,
            file_name=f"blog_summary_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="csv_summary",
        )
    with dl_col2:
        st.download_button(
            label="📥 전체 포스팅 CSV 다운로드",
            data=all_csv,
            file_name=f"blog_check_all_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="csv_all_top",
        )

    st.markdown("---")

    # ── 블로그별 상세 탭 ──
    tabs = st.tabs(blog_ids)

    for tab, blog_id in zip(tabs, blog_ids):
        with tab:
            df = pd.DataFrame(results[blog_id])
            df["작성일_dt"] = pd.to_datetime(df["작성일"], errors="coerce")
            total = len(df)
            exposure_checked = blog_id in st.session_state["exposure_done"]

            # ── 요약 ──
            st.markdown("### 요약")
            if exposure_checked:
                exposed = (df["노출여부"] == "노출").sum()
                not_exposed = (df["노출여부"] == "미노출").sum()
                exposure_rate = (exposed / total * 100) if total > 0 else 0
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("총 글 수", f"{total}개")
                col2.metric("노출", f"{exposed}개")
                col3.metric("미노출", f"{not_exposed}개")
                col4.metric("노출률", f"{exposure_rate:.1f}%")
            else:
                col1, col2 = st.columns(2)
                col1.metric("총 글 수", f"{total}개")
                col2.metric("노출 검사", "미실행")

            # 빈도 계산
            if not df["작성일_dt"].isna().all():
                date_range = (df["작성일_dt"].max() - df["작성일_dt"].min()).days
                if date_range > 0:
                    daily_avg = total / date_range
                    weekly_avg = daily_avg * 7
                    monthly_avg = daily_avg * 30

                    recent_30 = df[df["작성일_dt"] >= (datetime.now() - timedelta(days=30))]

                    col5, col6, col7, col8 = st.columns(4)
                    col5.metric("일당 평균", f"{daily_avg:.1f}회")
                    col6.metric("주당 평균", f"{weekly_avg:.1f}회")
                    col7.metric("월당 평균", f"{monthly_avg:.1f}회")
                    col8.metric("최근 30일", f"{len(recent_30)}개")

            # ── 월별 포스팅 차트 ──
            st.markdown("### 월별 포스팅 수")
            if not df["작성일_dt"].isna().all():
                monthly = df.set_index("작성일_dt").resample("M").size().reset_index(name="포스팅 수")
                monthly["월"] = monthly["작성일_dt"].dt.strftime("%Y-%m")
                st.bar_chart(monthly.set_index("월")["포스팅 수"])

            # ── 노출 검사 버튼 ──
            if not exposure_checked:
                st.markdown("---")
                if st.button(f"🔍 이 블로그 노출 검사", key=f"exposure_{blog_id}"):
                    posts = results[blog_id]
                    exposure_status = st.empty()
                    stop_col1, stop_col2 = st.columns([8, 2])
                    progress_bar = stop_col1.progress(0)
                    stop_button = stop_col2.button("⏹ 중지", key=f"stop_exposure_{blog_id}")
                    stopped = False

                    for i, post in enumerate(posts):
                        if stop_button:
                            exposure_status.warning(f"⚠️ **{blog_id}**: 중지됨 ({i}/{len(posts)}개 검사 완료)")
                            stopped = True
                            break
                        exposure_status.info(f"🔍 노출 검사 중... ({i+1}/{len(posts)}) - {post['제목'][:30]}...")
                        exposed = check_exposure(post["제목"], blog_id)
                        post["노출여부"] = "노출" if exposed else "미노출"
                        encoded_query = quote(f'"{post["제목"]}"')
                        post["검색확인"] = f"https://search.naver.com/search.naver?ssc=tab.nx.all&where=nexearch&query={encoded_query}&sm=tab_dgs&qdt=1"
                        progress_bar.progress((i + 1) / len(posts))
                        time.sleep(1.5)

                    if not stopped:
                        exposure_status.success(f"✅ **{blog_id}**: **{len(posts)}개** 글 노출 검사 완료!")
                    progress_bar.empty()

                    st.session_state["crawl_results"][blog_id] = posts
                    st.session_state["exposure_done"].add(blog_id)
                    st.rerun()

            # ── 빈도-노출률 상관관계 (노출 검사 완료 시) ──
            if exposure_checked:
                st.markdown("### 빈도-노출률 상관관계")
                df_freq = df.copy()
                df_freq["작성일_dt"] = pd.to_datetime(df_freq["작성일"], errors="coerce")
                df_freq = df_freq.dropna(subset=["작성일_dt"])
                df_freq["주차"] = df_freq["작성일_dt"].dt.to_period("W").apply(lambda r: r.start_time)
                weekly = df_freq.groupby("주차").agg(
                    포스팅수=("제목", "count"),
                    노출=("노출여부", lambda x: (x == "노출").sum()),
                    미노출=("노출여부", lambda x: (x == "미노출").sum()),
                ).reset_index()
                weekly["노출률"] = (weekly["노출"] / weekly["포스팅수"] * 100).round(1)

                if not weekly.empty:
                    weekly_display = weekly.copy()
                    weekly_display["주차"] = weekly_display["주차"].dt.strftime("%Y-%m-%d")
                    weekly_display.columns = ["주간 시작", "포스팅 수", "노출", "미노출", "노출률(%)"]
                    st.dataframe(weekly_display, use_container_width=True, hide_index=True)

                    st.markdown("#### 주간 포스팅 빈도 vs 노출률")
                    chart_data = weekly[["포스팅수", "노출률"]].rename(
                        columns={"포스팅수": "주간 포스팅 수", "노출률": "노출률(%)"}
                    )
                    st.scatter_chart(chart_data, x="주간 포스팅 수", y="노출률(%)")

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

            # ── 글 목록 ──
            st.markdown("### 글 목록")
            if exposure_checked:
                filter_option = st.radio(
                    "필터",
                    ["전체", "노출만", "미노출만"],
                    horizontal=True,
                    key=f"filter_{blog_id}",
                )
            else:
                filter_option = "전체"

            display_cols = ["제목", "작성일", "노출여부", "URL"]
            if exposure_checked:
                display_cols.append("검색확인")
            display_df = df[display_cols].copy()

            if filter_option == "노출만":
                display_df = display_df[display_df["노출여부"] == "노출"]
            elif filter_option == "미노출만":
                display_df = display_df[display_df["노출여부"] == "미노출"]

            col_config = {"URL": st.column_config.LinkColumn("URL")}
            if exposure_checked:
                col_config["검색확인"] = st.column_config.LinkColumn("검색확인")

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config=col_config,
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
