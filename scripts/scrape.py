#!/usr/bin/env python3
# -------------------------------------------------------------
#  OCHA Gaza snapshot scraper
#  - pick latest "Reported impact snapshot | Gaza Strip" PDF
#  - extract Palestinian fatalities
#  - update CSV (+ weekly diff)
#  - output 2 graphs and docs/index.html
# -------------------------------------------------------------
import os, re, io, datetime, requests, pdfplumber, pandas as pd
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
from dateutil import parser as dparser

ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_CSV   = os.path.join(ROOT, "data",  "fatalities.csv")
DOCS_DIR   = os.path.join(ROOT, "docs")
PNG_WEEKLY = os.path.join(DOCS_DIR, "fatalities_weekly.png")
PNG_CUM    = os.path.join(DOCS_DIR, "fatalities_cum.png")
HTML_FILE  = os.path.join(DOCS_DIR, "index.html")
LIST_URL   = "https://www.ochaopt.org/publications/snapshots"

os.makedirs(os.path.dirname(DATA_CSV), exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)

# ---------- 1. 最新 PDF URL を探す ----------
def find_latest_pdf():
    html = requests.get(LIST_URL, timeout=30).text
    soup = BeautifulSoup(html, "lxml")
    cand = []
    for a in soup.find_all("a"):
        t = a.get_text(strip=True)
        m = re.match(r"Reported impact snapshot \| Gaza Strip \((\d{1,2} \w+ \d{4})\)", t)
        if m:
            d = dparser.parse(m.group(1), dayfirst=True).date()
            cand.append((d, a["href"]))
    if not cand:
        raise RuntimeError("snapshot not found")
    cand.sort(reverse=True)
    return cand[0]  # (date, url)

# ---------- 2. PDF → 死亡者数 ----------
NBSP = "\u00A0"
def extract_deaths(url):
    pdf = pdfplumber.open(io.BytesIO(requests.get(url, timeout=60).content))
    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    pat = re.compile(rf"Palestinians[^{NBSP}\d]{{0,40}}([\d,{NBSP}\s]+)[^\w]{{0,20}}fatalities", re.I | re.S)
    m = pat.search(text)
    if not m:
        raise ValueError("deaths not found")
    return int(re.sub(r"[^\d]", "", m.group(1)))

# ---------- 3. CSV 更新 ----------
def update_csv(date, deaths):
    if os.path.exists(DATA_CSV):
        df = pd.read_csv(DATA_CSV, parse_dates=["date"])
    else:
        df = pd.DataFrame(columns=["date", "fatalities"])
    if (df["date"] == date).any():
        return df  # 既に登録済み
    df = pd.concat([df, pd.DataFrame([{"date": date, "fatalities": deaths}])])
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(DATA_CSV, index=False)
    return df

# ---------- 4. グラフ ----------
def make_plots(df):
    df = df.set_index("date")
    weekly = df["fatalities"].diff().fillna(df["fatalities"]).astype(int)

    plt.figure(figsize=(8,4))
    plt.bar(weekly.index, weekly.values)
    plt.title("Weekly increase in Palestinian fatalities (Gaza)")
    plt.ylabel("Deaths (weekly)")
    plt.tight_layout()
    plt.savefig(PNG_WEEKLY, dpi=150)
    plt.close()

    plt.figure(figsize=(8,4))
    plt.plot(df.index, df["fatalities"], marker="o")
    plt.title("Cumulative Palestinian fatalities (Gaza)")
    plt.ylabel("Deaths (cumulative)")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(PNG_CUM, dpi=150)
    plt.close()

# ---------- 5. HTML ----------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><meta charset="utf-8">
<head><title>Gaza fatalities – weekly tracker</title>
<style>body{{font-family:sans-serif;max-width:800px;margin:2rem auto;line-height:1.6}}</style>
</head>
<body>
<h1>Gaza: Reported Palestinian fatalities</h1>
<p>Source: OCHA oPt “Reported impact snapshot | Gaza Strip” PDFs<br>
Auto‑updated every Wednesday by GitHub Actions.</p>
<h2>Weekly increase</h2>
<img src="fatalities_weekly.png" alt="weekly fatalities">
<h2>Cumulative total</h2>
<img src="fatalities_cum.png" alt="cumulative fatalities">
<p>Latest data point: {latest_date} – {latest_total:,} deaths.</p>
<hr>
<p><small>Generated on {timestamp} (UTC)</small></p>
</body></html>
"""

def write_html(df):
    latest_date  = df.iloc[-1]["date"].date()
    latest_total = int(df.iloc[-1]["fatalities"])
    html = HTML_TEMPLATE.format(
        latest_date=latest_date,
        latest_total=latest_total,
        timestamp=datetime.datetime.utcnow().strftime("%Y‑%m‑%d %H:%M")
    )
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

# ---------- main ----------
if __name__ == "__main__":
    snap_date, page_url = find_latest_pdf()
    if not page_url.startswith("http"):
        page_url = "https://www.ochaopt.org" + page_url
    deaths = extract_deaths(page_url)
    df = update_csv(snap_date, deaths)
    make_plots(df)
    write_html(df)
    print(f"Snapshot {snap_date} → {deaths:,} deaths  |  CSV rows: {len(df)}")
