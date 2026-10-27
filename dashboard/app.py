"""서울 아파트 평당가격 분포 대시보드 (Streamlit).

fit_params(파이프라인이 매일 갱신)와 deals를 읽어서 보여준다.
대시보드는 피팅을 다시 계산하지 않고, 저장된 파라미터로 곡선을 그린다.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import importlib

import apt_pipeline.config
import apt_pipeline.db
import apt_pipeline.fitting
import apt_pipeline.records

# src 코드를 고쳐도 컨테이너 재시작 없이 새로고침만으로 반영되도록 매번 다시 읽는다
for _m in (apt_pipeline.config, apt_pipeline.records, apt_pipeline.db, apt_pipeline.fitting):
    importlib.reload(_m)

from apt_pipeline import config, fitting  # noqa: E402
from apt_pipeline.db import Database  # noqa: E402

# 계열 색 (blue / orange / aqua) — 같은 축에 3개 이하만 사용
C_PL, C_LN, C_DATA = "#2a78d6", "#eb6834", "#8a8984"
C_FIT = "#e34948"                     # 꼬리 피팅선 (빨강)
C_SIG = {True: "#2a78d6", False: "#eb6834"}
NOT_SIG = "#c3c2b7"

st.set_page_config(page_title="서울 아파트 가격 분포", layout="wide")


def get_db():
    return Database()


@st.cache_data(ttl=60)
def load_fits():
    return get_db().query("SELECT * FROM fit_params ORDER BY ym")


@st.cache_data(ttl=60)
def load_month(ym):
    return get_db().month_complexes(ym)


@st.cache_data(ttl=60)
def last_collect():
    df = get_db().query("SELECT MAX(run_at) AS t FROM collect_log")
    return df["t"].iloc[0]


def full_months(df):
    """빈 달(예: 2020~2023)은 NaN으로 채워서 선이 이어지지 않게 한다."""
    idx = pd.period_range(df["ym"].min(), df["ym"].max(), freq="M").strftime("%Y-%m")
    return df.set_index("ym").reindex(idx).rename_axis("ym").reset_index()


SUP = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def _sci(v):
    """2000 → '2×10³', 1000 → '10³', 0.001 → '10⁻³'"""
    e = int(np.floor(np.log10(v) + 1e-9))
    m = round(v / 10 ** e)
    p = f"10{str(e).translate(SUP)}"
    return p if m == 1 else f"{m}×{p}"


def _log_ticks(lo, hi, mantissas):
    vals = [m * 10 ** e for e in range(int(np.floor(np.log10(lo))), int(np.ceil(np.log10(hi))) + 1)
            for m in mantissas if lo <= m * 10 ** e <= hi]
    return vals, [_sci(v) for v in vals]


def style_loglog(fig, x, y, title):
    """참고 그래프 스타일: log-log, 10의 거듭제곱 눈금, 위아래 여백, 범례는 오른쪽 위 안쪽."""
    xlo, xhi = x.min() / 1.25, x.max() * 1.25
    ylo, yhi = 10 ** np.floor(np.log10(y.min()) - 0.5), 10 ** np.ceil(np.log10(y.max()) + 0.2)
    xv, xt = _log_ticks(xlo, xhi, [1, 2, 3, 5])
    yv, yt = _log_ticks(ylo, yhi, [1])
    axis = dict(type="log", showline=True, mirror=True, ticks="inside", showgrid=False,
                linecolor="rgba(128,128,128,0.8)", zeroline=False)
    fig.update_xaxes(**axis, range=[np.log10(xlo), np.log10(xhi)], tickvals=xv, ticktext=xt,
                     title="P (평당가격, 만원/평)", minor=dict(ticks="inside", showgrid=False))
    fig.update_yaxes(**axis, range=[np.log10(ylo), np.log10(yhi)], tickvals=yv, ticktext=yt,
                     title="f(P)", minor=dict(ticks="inside", showgrid=False))
    fig.update_layout(title=dict(text=title, x=0.5, xanchor="center"), height=420,
                      margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(x=0.98, y=0.98, xanchor="right", yanchor="top",
                                  bordercolor="rgba(128,128,128,0.6)", borderwidth=1))


def style_linear(fig, x, y, title):
    """몸통용: 선형 축, 테두리·안쪽 눈금, 범례 오른쪽 위 안쪽. 세로축은 10의 거듭제곱 표기."""
    pad = (x.max() - x.min()) * 0.05
    axis = dict(showline=True, mirror=True, ticks="inside", showgrid=False,
                linecolor="rgba(128,128,128,0.8)", zeroline=False)
    fig.update_xaxes(**axis, range=[x.min() - pad, x.max() + pad], title="P (평당가격, 만원/평)",
                     separatethousands=True)
    fig.update_yaxes(**axis, range=[0, y.max() * 1.3], title="f(P)",
                     exponentformat="power", showexponent="all")
    fig.update_layout(title=dict(text=title, x=0.5, xanchor="center"), height=420,
                      margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(x=0.98, y=0.98, xanchor="right", yanchor="top",
                                  bordercolor="rgba(128,128,128,0.6)", borderwidth=1))


def line_fig(title, y_title, height=280):
    fig = go.Figure()
    fig.update_layout(title=title, height=height, hovermode="x unified",
                      margin=dict(l=10, r=10, t=50, b=10),
                      yaxis_title=y_title, legend=dict(orientation="h", y=1.12))
    fig.update_xaxes(showgrid=False)
    return fig


fits = load_fits()
if fits.empty:
    st.info("아직 피팅 결과가 없습니다. Airflow에서 DAG를 먼저 실행하세요.")
    st.stop()

# ---------------- 필터 ----------------
names = {config.SEOUL: "서울 전체", **config.SEOUL_GU}
st.title("서울 아파트 단지별 평당가격 분포")
c1, c2, c3 = st.columns([2, 2, 3])
region = c1.selectbox("지역", list(names), format_func=names.get)
f = fits[fits["region"] == region].copy()
months = f["ym"].tolist()
ym = c2.selectbox("분포를 볼 월", months[::-1])
c3.caption(f"마지막 수집: {last_collect() or '-'}  ·  분석 단위: 단지별 월평균 평당가격  ·  "
           f"거래 단지가 {config.MIN_SAMPLE}개 미만인 달은 피팅하지 않음")

# ---------------- 핵심 지표 ----------------
cur = f[f["ym"] == ym].iloc[0]
fmt = lambda v, p: f"{v:.{p}f}" if pd.notna(v) else "-"
k = st.columns(6)
k[0].metric("거래된 단지 수", f"{int(cur['n']):,}")
k[1].metric("꼬리 경계 (상위 5%)", f"{cur['pl_xmin']:,.0f}만원/평" if pd.notna(cur["pl_xmin"]) else "-")
k[2].metric("몸통 σ (log-normal MLE)", fmt(cur["ln_sigma"], 3))
k[3].metric("꼬리 α (OLS)", fmt(cur["ols_alpha"], 2))
k[4].metric("지니계수", fmt(cur["gini"], 3))
if pd.notna(cur["lr_r"]):
    winner = ("power-law" if cur["lr_r"] > 0 else "log-normal") if cur["lr_p"] < 0.1 else "판단 보류"
    k[5].metric("꼬리 비교", winner, f"R={cur['lr_r']:.1f}, p={cur['lr_p']:.2f}",
                delta_color="off")

# ---------------- 1. 몸통 / 꼬리 분포 ----------------
d = load_month(ym)
if region != config.SEOUL:
    d = d[d["sgg_cd"] == region]
x = d["price_per_pyeong"].to_numpy()
p1, p2 = st.columns(2)

if len(x) and pd.notna(cur["ln_mu"]):
    xmin = cur["pl_xmin"]
    body, tail = x[x < xmin], x[x >= xmin]

    # 몸통: 구간 가운데 밀도(점) + 잘린 log-normal 밀도(MLE, 선). 점은 그림용, 피팅엔 안 씀
    mids, dens = fitting.histogram_mids(body, config.BODY_BINS)   # 몸통만 선형 60구간
    keep = dens > 0
    fig = go.Figure()
    fig.add_scatter(x=mids[keep], y=dens[keep], mode="markers", name="data",
                    marker=dict(symbol="circle-open", size=9, color=C_PL, line=dict(width=1.5)),
                    hovertemplate="%{x:,.0f}만원/평<br>f(P) = %{y:.2e}<extra></extra>")
    g = np.linspace(mids[keep].min(), mids[keep].max(), 300)
    fig.add_scatter(x=g, y=fitting.body_pdf(g, cur["ln_mu"], cur["ln_sigma"], xmin), mode="lines",
                    name=f"μ = {cur['ln_mu']:.4f}, σ = {cur['ln_sigma']:.4f}",
                    line=dict(color=C_FIT, width=2.5))
    style_linear(fig, mids[keep], dens[keep],
                 title=f"{ym} 몸통 (하위 95%, {len(body):,}개 단지) · log-normal")
    p1.plotly_chart(fig, use_container_width=True, theme="streamlit")

    # 꼬리: 구간 가운데 밀도(점) + c·x^(-α) 최소제곱
    xmax = fitting.tail_upper(x)
    ols = fitting.ols_powerlaw(tail, xmax=xmax) if len(tail) >= config.MIN_TAIL else None
    if ols:
        a, c, mids, dens, used = ols                      # 꼬리만 선형 60구간
        keep = dens > 0                                  # 0인 구간은 로그축에 못 그림
        fit_pts = keep & used                            # 극단값(상위 0.05%) 구간은 피팅·그림에서 뺌
        fig = go.Figure()
        fig.add_scatter(x=mids[fit_pts], y=dens[fit_pts], mode="markers", name="data",
                        marker=dict(symbol="circle-open", size=9, color=C_PL, line=dict(width=1.5)),
                        hovertemplate="%{x:,.0f}만원/평<br>f(P) = %{y:.2e}<extra></extra>")
        g = np.geomspace(mids[fit_pts].min(), mids[fit_pts].max(), 100)
        fig.add_scatter(x=g, y=c * g ** (-a), mode="lines", name=f"alpha = {a:.5f}",
                        line=dict(color=C_FIT, width=2.5))
        style_loglog(fig, mids[fit_pts], dens[fit_pts],
                     title=f"{ym} 꼬리 (상위 5%, {len(tail):,}개 단지) · power-law")
        p2.plotly_chart(fig, use_container_width=True, theme="streamlit")
    else:
        p2.info(f"꼬리 단지가 {len(tail)}개라 power-law를 맞추지 않았습니다 (최소 {config.MIN_TAIL}개).")

with st.expander("읽는 법"):
    st.markdown(
        "- 점 하나는 그 달에 거래된 **단지 하나의 평균 평당가격**입니다. 한 단지에서 여러 건이 거래돼도 하나로 셉니다.\n"
        "- 상위 5% 지점(꼬리 경계)을 기준으로 **몸통은 log-normal, 꼬리는 power-law**로 따로 맞췄습니다.\n"
        "- 상위 5% 경계에서 먼저 몸통과 꼬리를 나눈 뒤, 각각 **같은 폭 60구간** 히스토그램을 만들어 구간 가운데의 밀도 f(P)를 점으로 찍습니다. 밀도는 몸통·꼬리 각자 안에서 합이 1입니다. 몸통은 일반 축, 꼬리는 log-log 축이며 꼬리에서 비어 있는 구간은 표시되지 않습니다.\n"
        "- 몸통은 위쪽이 잘린 분포라 잘린 log-normal을 최대우도(MLE)로 추정합니다. σ가 클수록 가격이 넓게 퍼져 있습니다.\n"
        "- 꼬리 α는 기존 분석과 같은 방식(히스토그램 밀도에 최소제곱)으로 구합니다. log-log 그래프에서 점들이 직선에 가까울수록 power-law에 잘 맞습니다.\n"
        "- α가 작을수록 꼬리가 두껍습니다(초고가 거래 비중이 큼).\n"
        "- 꼬리 비교는 꼬리에서 power-law와 log-normal의 로그우도비 R입니다. R>0이면 power-law, R<0이면 log-normal 쪽이고, p가 0.1 이상이면 판단을 보류합니다."
    )

# ---------------- 2. 파라미터 추이 ----------------
ft = full_months(f)
a1, a2 = st.columns(2)

fig = line_fig("꼬리 지수 α 추이", "α")
fig.add_scatter(x=ft["ym"], y=ft["ols_alpha"], name="α (OLS)", mode="lines+markers",
                line=dict(color=C_PL, width=2), marker=dict(size=8), showlegend=False)
a1.plotly_chart(fig, use_container_width=True, theme="streamlit")

fig = line_fig("몸통 σ 추이 (가격 퍼짐 정도)", "σ")
fig.add_scatter(x=ft["ym"], y=ft["ln_sigma"], name="σ", mode="lines+markers",
                line=dict(color=C_LN, width=2), marker=dict(size=8), showlegend=False)
a2.plotly_chart(fig, use_container_width=True, theme="streamlit")

b1, b2 = st.columns(2)
fig = line_fig("지니계수 추이", "Gini")
fig.add_scatter(x=ft["ym"], y=ft["gini"], name="Gini", mode="lines+markers",
                line=dict(color=C_PL, width=2), marker=dict(size=8), showlegend=False)
b1.plotly_chart(fig, use_container_width=True, theme="streamlit")

sig = ft["lr_p"] < 0.1
colors = [C_SIG[r > 0] if s else NOT_SIG for r, s in zip(ft["lr_r"].fillna(0), sig)]
fig = line_fig("꼬리 비교 R (위: power-law 우세, 아래: log-normal 우세, 회색: 유의하지 않음)", "R")
fig.add_bar(x=ft["ym"], y=ft["lr_r"], marker_color=colors, showlegend=False,
            customdata=ft["lr_p"], hovertemplate="%{x}<br>R=%{y:.2f}<br>p=%{customdata:.3f}<extra></extra>")
fig.add_hline(y=0, line=dict(color=C_DATA, width=1))
b2.plotly_chart(fig, use_container_width=True, theme="streamlit")

with st.expander("파라미터 표"):
    st.dataframe(f.drop(columns=["region"]).set_index("ym").iloc[::-1],
                 use_container_width=True)
