#!/usr/bin/env python3
# ==============================================================
#  scrape.py  ―  Weekly OCHA Gaza “Reported Impact Snapshot”
#               ▸ 最新 PDF を検出
#               ▸ パレスチナ側 死亡者数を抽出
#               ▸ data/fatalities.csv に追記
#               ▸ 推移グラフ (plots/*.png) を更新
#
#   2025‑05‑08  Improved: robust PDF text parsing, graceful OCR
# ==============================================================

import os, re, sys, io, tempfile, datetime, logging, json
import requests, pdfplumber, pandas as pd
from bs4 import BeautifulSoup
from dateutil import parser as dparser
import matplotlib.pyplot as plt

# ---------- 設定 ----------
ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_CSV = os.path.join(ROOT, "data", "fatalities.csv")
PLOT_PNG = os.path.join(ROOT, "plots", "palestinian_fatalities.png")
LIST_URL = "https://www.ochaopt.org/publications/snapshots"

# OCR フォールバック用（インストールしていなければ自動でスキップ）
try:
    import pytesseract
    from pdf2image import convert_from_bytes
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ---------- 1) 最新 PDF の URL を探す ----------
def find_latest_pdf_url():
    """Snapshots 一覧ページから最新日付の PDF を推測し HEAD で存在確認。
       見つからない場合は ReliefWeb API をフォールバック。"""
    logging.info("Fetching list page…")
    html = requests.get(LIST_URL, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")   # lxml も OK

    snapshots = []
    for a in soup.find_all("a"):
        title = a.get_text(strip=True)
        m = re.match(r"Reported impact snapshot \| Gaza Strip \((\d{1,2} \w+ \d{4})\)", title, re.I)
        if m:
            date_obj = dparser.parse(m.group(1), dayfirst=True)
            snapshots.append(date_obj)

    if not snapshots:
        raise RuntimeError("一覧ページから日付を取得できません")

    latest = max(snapshots)
    day   = f"{latest.day:02d}"
    month = latest.strftime("%B")   # April, May, …
    year  = latest.year

    # よくある 3 パターンを順に試す
    cand = [
        f"https://www.ochaopt.org/sites/default/files/Gaza_Reported_Impact_Snapshot_{day}_{month}_{year}%20final.pdf",
        f"https://www.ochaopt.org/sites/default/files/Gaza_Reported_Impact_Snapshot_{day}_{month}_{year}-final.pdf",
        f"https://www.ochaopt.org/sites/default/files/Gaza_Reported_Impact_Snapshot_{day}_{month}_{year}.pdf",
    ]
    for url in cand:
        if requests.head(url, timeout=30).ok:
            logging.info("Found PDF: %s", url)
            return url, latest

    # ---- ReliefWeb API fallback ----
    logging.info("Trying ReliefWeb API fallback…")
    api = (
        "https://api.reliefweb.int/v1/reports?"
        "appname=ochascrape&query[value]=Gaza%20Reported%20Impact%20Snapshot"
        "&query[field]=title&sort[]=date:desc&filter[field]=name&pager[limit]=1"
    )
    data = requests.get(api, timeout=30).json()
    try:
        url = data["data"][0]["fields"]["attachments"][0]["url"]
        latest = dparser.parse(data["data"][0]["fields"]["date"], fuzzy=True)
        logging.info("Found via ReliefWeb: %s", url)
        return url, latest
    except Exception:
        raise RuntimeError("ReliefWeb API でも PDF URL が見つかりません")

# ---------- 2) PDF から死亡者数を抽出 ----------
NBSP  = "\u00A0"
NNBSP = "\u202F"
NUM_CHARS = rf"0-9,{NBSP}{NNBSP}\s"

def extract_deaths(pdf_url):
    logging.info("Downloading PDF…")
    buf = requests.get(pdf_url, timeout=60).content

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(buf)
        pdf_path = tmp.name

    # ------- try text layer -------
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    if not text.strip():
        logging.warning("No text layer detected.")
    else:
        num = _parse_deaths_from_text(text)
        if num:
            return num

    # ------- OCR fallback -------
    if OCR_AVAILABLE:
        logging.info("Trying OCR fallback… (this may take ~30s)")
        images = convert_from_bytes(buf, dpi=300)
        ocr_text = "\n".join(pytesseract.image_to_string(im) for im in images)
        num = _parse_deaths_from_text(ocr_text)
        if num:
            return num

    raise RuntimeError("PDF から死亡者数が抽出できません")

def _parse_deaths_from_text(text: str) -> int | None:
    """text 内から死亡者数をできるだけ柔軟に抜き出す。見つからなければ None"""
    # 1) 「palestinians … fatalities」パターンを広めに取る
    pat1 = re.compile(
        rf"palestinians[^{NUM_CHARS}]{{0,60}}([{NUM_CHARS}]{{5,}})[^a-z]{{0,20}}fatalities",
        re.I | re.S,
    )
    m = pat1.search(text)
    if not m:
        # 2) Palestinians を含む行の数字塊
        for line in text.splitlines():
            if "palestinians" in line.lower():
                nums = re.findall(rf"[{NUM_CHARS}]{{5,}}", line)
                if nums:
                    m = nums[0]
                    break
    if not m:
        return None

    num_str = m if isinstance(m, str) else m.group(1)
    num_str = re.sub(r"[^\d]", "", num_str)   # 非数字を全部除去 → '52653'
    try:
        return int(num_str)
    except ValueError:
        return None

# ---------- 3) CSV & グラフ ----------
def main():
    pdf_url, snap_date = find_latest_pdf_url()
    deaths = extract_deaths(pdf_url)
    logging.info("Snapshot %s  deaths = %s", snap_date.date(), f"{deaths:,}")

    # --- CSV update ---
    os.makedirs(os.path.dirname(DATA_CSV), exist_ok=True)
    if os.path.exists(DATA_CSV):
        df = pd.read_csv(DATA_CSV, parse_dates=["date"])
    else:
        df = pd.DataFrame(columns=["date", "fatalities"])

    if (df["date"] == snap_date).any():
        logging.info("This date already exists in CSV → no append.")
    else:
        df = pd.concat([df, pd.DataFrame([{"date": snap_date, "fatalities": deaths}])])
        df = df.sort_values("date").reset_index(drop=True)
        df.to_csv(DATA_CSV, index=False)
        logging.info("🟢 New data appended: %s  %s", snap_date.date(), f"{deaths:,}")

    # --- Plot update ---
    os.makedirs(os.path.dirname(PLOT_PNG), exist_ok=True)
    plt.figure(figsize=(9, 4))
    plt.plot(df["date"], df["fatalities"], marker="o")
    plt.title("Gaza: Reported Palestinian fatalities (cumulative)")
    plt.ylabel("Fatalities")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(PLOT_PNG, dpi=150)
    logging.info("Plot saved to %s", PLOT_PNG)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error("%s: %s", e.__class__.__name__, e)
        sys.exit(1)
