#!/usr/bin/env python
# coding: utf-8
import re, os, io, sys, datetime, tempfile, json
import requests, pdfplumber, pandas as pd
from bs4 import BeautifulSoup
from dateutil import parser as dparser
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_CSV = os.path.join(ROOT, "data", "fatalities.csv")
PLOT_PNG = os.path.join(ROOT, "plots", "palestinian_fatalities.png")
LIST_URL  = "https://www.ochaopt.org/publications/snapshots"

os.makedirs(os.path.dirname(DATA_CSV), exist_ok=True)
os.makedirs(os.path.dirname(PLOT_PNG), exist_ok=True)

# ------------------------------------------------------------
# 1) 最新 PDF URL を探す（まず一覧ページ → なければ ReliefWeb API にフォールバック）
# ------------------------------------------------------------
def find_latest_pdf_url():
    html = requests.get(LIST_URL, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")

    # 「Reported impact snapshot | Gaza Strip (7 May 2025)」のような <a> を探す
    snapshots = []
    for a in soup.find_all("a"):
        title = a.get_text(strip=True)
        m = re.match(r"Reported impact snapshot \| Gaza Strip \((\d{1,2} \w+ \d{4})\)", title)
        if m:
            date_str = m.group(1)
            date_obj = dparser.parse(date_str, dayfirst=True)      # 例: 2025-05-07
            snapshots.append(date_obj)
    if not snapshots:
        raise RuntimeError("一覧ページで日付が取れませんでした")

    latest_date = max(snapshots)
    day = f"{latest_date.day:02d}"
    month = latest_date.strftime("%B")   # April, May, ...
    year = latest_date.year
    patterns = [
        f"https://www.ochaopt.org/sites/default/files/Gaza_Reported_Impact_Snapshot_{day}_{month}_{year}%20final.pdf",
        f"https://www.ochaopt.org/sites/default/files/Gaza_Reported_Impact_Snapshot_{day}_{month}_{year}-final.pdf",
        f"https://www.ochaopt.org/sites/default/files/Gaza_Reported_Impact_Snapshot_{day}_{month}_{year}.pdf",
    ]

    for url in patterns:
        r = requests.head(url, timeout=30)
        if r.ok:
            return url, latest_date

    # フォールバック：ReliefWeb API
    api = ("https://api.reliefweb.int/v1/reports?"
           "appname=ochascrape&query[value]=Gaza%20Reported%20Impact%20Snapshot"
           "&query[field]=title&sort[]=date:desc&filter[field]=name&pager[limit]=1")
    data = requests.get(api, timeout=30).json()
    try:
        att = data["data"][0]["fields"]["attachments"][0]
        return att["url"], dparser.parse(data["data"][0]["fields"]["date"], fuzzy=True)
    except Exception as e:
        raise RuntimeError("ReliefWeb API でも PDF URL が見つかりません")

# ------------------------------------------------------------
# 2) PDF ダウンロード → 死亡者数を抽出
# ------------------------------------------------------------
def extract_deaths(pdf_url):
    buf = requests.get(pdf_url, timeout=60).content
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(buf)
        pdf_path = tmp.name

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    # 1) 「palestinians」「fatalities」が近接している箇所を狙う
    pat = re.compile(
        r"palestinians[^0-9]{0,40}([\d,]{3,})[^a-z]{0,20}fatalities",
        re.I | re.S,
    )
    m = pat.search(text)

    # 2) 見つからなければ行単位でスキャン
    if not m:
        for line in text.splitlines():
            if "palestinians" in line.lower():
                nums = re.findall(r"\d{1,3}(?:,\d{3})+", line)
                if nums:
                    m = nums[0]
                    break

    if not m:
        raise RuntimeError("PDF から死亡者数が抽出できません")

    num_str = m if isinstance(m, str) else m.group(1)
    return int(num_str.replace(",", ""))

# ------------------------------------------------------------
def main():
    pdf_url, snap_date = find_latest_pdf_url()
    deaths = extract_deaths(pdf_url)

    if os.path.exists(DATA_CSV):
        df = pd.read_csv(DATA_CSV, parse_dates=["date"])
    else:
        df = pd.DataFrame(columns=["date", "fatalities"])

    if not ((df["date"] == snap_date).any()):
        df = pd.concat([df, pd.DataFrame([{"date": snap_date, "fatalities": deaths}])])
        df = df.sort_values("date").reset_index(drop=True)
        df.to_csv(DATA_CSV, index=False)
        print(f"🟢 New data appended: {snap_date.date()}  {deaths:,}")
    else:
        print("No new weekly snapshot – nothing to add.")

    # ---- グラフ更新 ----
    plt.figure(figsize=(9, 4))
    plt.plot(df["date"], df["fatalities"], marker="o")
    plt.title("Gaza: Reported Palestinian fatalities (cumulative)")
    plt.ylabel("Fatalities")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(PLOT_PNG, dpi=150)
    print("Plot updated.")

if __name__ == "__main__":
    main()
