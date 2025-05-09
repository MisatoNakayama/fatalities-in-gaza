#!/usr/bin/env python3
# -------------------------------------------------------------------
#  scrape.py  ―  OCHA “Reported impact snapshot | Gaza Strip” 自動収集
#               ▸ 最新スナップショット HTML → PDF を解決
#               ▸ パレスチナ人死亡者数を抽出
#               ▸ data/fatalities.csv に追記
#               ▸ 週次増分グラフ & 累計グラフを docs/ に出力
#               ▸ docs/index.html を更新（GitHub Pages 用）
# -------------------------------------------------------------------
import os, re, io, datetime, requests, pdfplumber, pandas as pd
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
from dateutil import parser as dparser

# ------------- パス設定 ------------------------------------------------------
ROOT        = os.path.dirname(os.path.dirname(__file__))
DATA_CSV    = os.path.join(ROOT, "data", "fatalities.csv")
DOCS_DIR    = os.path.join(ROOT, "docs")
PNG_WEEKLY  = os.path.join(DOCS_DIR, "fatalities_weekly.png")
PNG_CUM     = os.path.join(DOCS_DIR, "fatalities_cum.png")
HTML_FILE   = os.path.join(DOCS_DIR, "index.html")

LIST_URL    = "https://www.ochaopt.org/publications/snapshots"
BASE_URL    = "https://www.ochaopt.org"

os.makedirs(os.path.dirname(DATA_CSV), exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)

# ------------- 1) 最新スナップショットの HTML ページ -------------------------
def find_latest_snapshot_page() -> tuple[datetime.date, str]:
    """一覧ページを解析し、最も新しい snapshot の (日付, HTML URL) を返す"""
    soup = BeautifulSoup(requests.get(LIST_URL, timeout=30).text, "lxml")
    snaps = []
    for a in soup.find_all("a", href=True):
        t = a.get_text(strip=True)
        m = re.match(r"Reported impact snapshot \| Gaza Strip \((\d{1,2} \w+ \d{4})\)", t)
        if m:
            d = dparser.parse(m.group(1), dayfirst=True).date()
            url = a["href"]
            if not url.startswith("http"):
                url = BASE_URL + url
            snaps.append((d, url))
    snaps.sort(reverse=True)
    if not snaps:
        raise RuntimeError("snapshot link not found on list page")
    return snaps[0]  # (date, html_url)

# ------------- 2) HTML → PDF ダウンロードリンクを解決 ------------------------
def resolve_pdf_url(page_url: str) -> str:
    soup = BeautifulSoup(requests.get(page_url, timeout=30).text, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            return href if href.startswith("http") else BASE_URL + href
    raise ValueError("PDF link not found in snapshot page")

# ------------- 3) PDF から死亡者数を抽出 --------------------------------------
NBSP = "\u00A0"   # 不換改行スペース
def extract_deaths(pdf_url: str) -> int:
    r = requests.get(pdf_url, timeout=60)
    r.raise_for_status()
    if not r.headers.get("content-type", "").lower().startswith("application/pdf"):
        raise ValueError("resolved URL is not a PDF")
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    pat = re.compile(
        rf"Palestinians[^\d{NBSP}]{{0,40}}([\d,{NBSP}\s]+)[^\w]{{0,20}}fatalities",
        re.I | re.S,
    )
    m = pat.search(text)
    if not m:
        raise ValueError("fatalities number not found in PDF")
    return int(re.sub(r"[^\d]", "", m.group(1)))

# ------------- 4) CSV を更新 --------------------------------------------------
def update_csv(date: datetime.date, deaths: int) -> pd.DataFrame:
    if os.path.exists(DATA_CSV):
        df = pd.read_csv(DATA_CSV, parse_dates=["date"])
    else:
        df = pd.DataFrame(columns=["date", "fatalities"])
    if (df["date"] == pd.Timestamp(date)).any():
        return df
    df = pd.concat([df, pd.DataFrame([{"date": date, "fatalities": deaths}])])
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(DATA_CSV, index=False)
    return df

# ------------- 5) グラフ作成 --------------------------------------------------
def make_plots(df: pd.DataFrame):
    df = df.set_index("date")
    weekly = df["fatalities"].diff().fillna(df["fatalities"]).astype(int)

    plt.figure(figsize=(8, 4))
    plt.bar(weekly.index, weekly.values)
    plt.title("Weekly increase in Palestinian fatalities (Gaza)")
    plt.ylabel("Deaths / week")
    plt.tight_layout()
    plt.savefig(PNG_WEEKLY, dpi=150)
    plt.close()

    plt.figure(figsize=(8, 4))
    plt.plot(df.index, df["fatalities"], marker="o")
    plt.title("Cumulative Palestinian fatalities (Gaza)")
    plt.ylabel("Deaths (cumulative)")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(PNG_CUM, dpi=150)
    plt.close()

# ------------- 6) HTML レポート ------------------------------------------------
HTML_TEMPLATE = """<!doctype html><meta charset="utf-8">
<title>Gaza fatalities – weekly tracker</title>
<style>
body{{font-family:system-ui,Arial,sans-serif;max-width:820px;margin:2rem auto;line-height:1.6}}
h1,h2{{margin-top:2rem}}
img{{max-width:100%;height:auto;}}
footer{{margin-top:3rem;font-size:.9em;color:#666}}
</style>
<h1>Gaza: Reported Palestinian fatalities</h1>
<p>Source: OCHA oPt “Reported impact snapshot | Gaza Strip” PDFs.  
This page auto‑updates every Thursday (JST) via GitHub Actions.</p>
<h2>Weekly increase</h2>
<img src="fatalities_weekly.png" alt="weekly fatalities bar chart">
<h2>Cumulative total</h2>
<img src="fatalities_cum.png" alt="cumulative fatalities line chart">
<p>Latest data point: <strong>{latest_date}</strong> — <strong>{latest_total:,}</strong> deaths.</p>
<footer>Generated on {timestamp} UTC</footer>
"""

def write_html(df: pd.DataFrame):
    latest_date  = df.iloc[-1]["date"].date()
    latest_total = int(df.iloc[-1]["fatalities"])
    html = HTML_TEMPLATE.format(
        latest_date=latest_date,
        latest_total=latest_total,
        timestamp=datetime.datetime.utcnow().strftime("%Y‑%m‑%d %H:%M")
    )
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

# ------------- main ----------------------------------------------------------
if __name__ == "__main__":
    snap_date, page_url = find_latest_snapshot_page()
    pdf_url = resolve_pdf_url(page_url)
    deaths  = extract_deaths(pdf_url)
    df      = update_csv(snap_date, deaths)
    make_plots(df)
    write_html(df)
    print(f"✔  {snap_date}  {deaths:,} deaths   (CSV rows: {len(df)})")
