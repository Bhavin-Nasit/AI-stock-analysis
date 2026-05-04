from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
from flask import Flask, Response, jsonify, render_template_string, request, send_file, url_for


app = Flask(__name__)


DISCLAIMER = (
    "This dashboard is for educational and research purposes only. It is not "
    "financial advice, investment advice, or a recommendation to buy, sell, or "
    "hold any security. Verify all data independently and consult a licensed "
    "financial advisor before making investment decisions."
)


WEIGHTS = {
    "technical": 0.25,
    "fundamental": 0.25,
    "sentiment": 0.20,
    "risk": 0.15,
    "thesis": 0.15,
}


DEFAULT_SCAN_UNIVERSE = "NIFTY 500"
FORCE_REFRESH_SCAN_UNIVERSE = "NIFTY 500"
NIFTY100_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"
NIFTY500_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
NIFTY100_FALLBACK_SYMBOLS = [
    "ABB.NS",
    "ADANIENSOL.NS",
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "ADANIPOWER.NS",
    "AMBUJACEM.NS",
    "APOLLOHOSP.NS",
    "ASIANPAINT.NS",
    "AXISBANK.NS",
    "BAJAJ-AUTO.NS",
    "BAJFINANCE.NS",
    "BAJAJFINSV.NS",
    "BAJAJHLDNG.NS",
    "BANKBARODA.NS",
    "BEL.NS",
    "BHARTIARTL.NS",
    "BHEL.NS",
    "BOSCHLTD.NS",
    "BPCL.NS",
    "BRITANNIA.NS",
    "CANBK.NS",
    "CHOLAFIN.NS",
    "CIPLA.NS",
    "COALINDIA.NS",
    "DABUR.NS",
    "DIVISLAB.NS",
    "DLF.NS",
    "DMART.NS",
    "DRREDDY.NS",
    "EICHERMOT.NS",
    "GAIL.NS",
    "GODREJCP.NS",
    "GRASIM.NS",
    "HAL.NS",
    "HAVELLS.NS",
    "HCLTECH.NS",
    "HDFCBANK.NS",
    "HDFCLIFE.NS",
    "HEROMOTOCO.NS",
    "HINDALCO.NS",
    "HINDUNILVR.NS",
    "HINDZINC.NS",
    "ICICIBANK.NS",
    "ICICIGI.NS",
    "ICICIPRULI.NS",
    "IDFCFIRSTB.NS",
    "INDIGO.NS",
    "INDUSINDBK.NS",
    "INFY.NS",
    "IOC.NS",
    "IRFC.NS",
    "ITC.NS",
    "JINDALSTEL.NS",
    "JIOFIN.NS",
    "JSWENERGY.NS",
    "JSWSTEEL.NS",
    "KOTAKBANK.NS",
    "LICI.NS",
    "LODHA.NS",
    "LT.NS",
    "M&M.NS",
    "MANKIND.NS",
    "MARUTI.NS",
    "MAXHEALTH.NS",
    "MCDOWELL-N.NS",
    "MOTHERSON.NS",
    "NAUKRI.NS",
    "NESTLEIND.NS",
    "NTPC.NS",
    "ONGC.NS",
    "PFC.NS",
    "PIDILITIND.NS",
    "PNB.NS",
    "POLYCAB.NS",
    "POWERGRID.NS",
    "RECLTD.NS",
    "RELIANCE.NS",
    "SBILIFE.NS",
    "SBIN.NS",
    "SHREECEM.NS",
    "SHRIRAMFIN.NS",
    "SIEMENS.NS",
    "SUNPHARMA.NS",
    "TATACONSUM.NS",
    "TATAMOTORS.NS",
    "TATAPOWER.NS",
    "TATASTEEL.NS",
    "TCS.NS",
    "TECHM.NS",
    "TITAN.NS",
    "TORNTPOWER.NS",
    "TORNTPHARM.NS",
    "TRENT.NS",
    "TVSMOTOR.NS",
    "ULTRACEMCO.NS",
    "VBL.NS",
    "VEDL.NS",
    "WIPRO.NS",
    "ZYDUSLIFE.NS",
    "ZOMATO.NS",
]


INDEX_UNIVERSES = {
    "NIFTY 100": {
        "csv_url": NIFTY100_CSV_URL,
        "env_var": "NIFTY100_SYMBOLS",
        "fallback_symbols": NIFTY100_FALLBACK_SYMBOLS,
        "min_symbols": 80,
        "slug": "nifty100",
    },
    "NIFTY 500": {
        "csv_url": NIFTY500_CSV_URL,
        "env_var": "NIFTY500_SYMBOLS",
        "fallback_symbols": [],
        "min_symbols": 400,
        "slug": "nifty500",
    },
}


SCAN_CACHE_TTL_HOURS = int(os.getenv("SCAN_CACHE_TTL_HOURS", "24"))
SCAN_CACHE_DIR = Path(os.getenv("SCAN_CACHE_DIR", "/tmp/indian-stock-score-dashboard"))
SCAN_LOCK = threading.Lock()
SCAN_STATE: Dict[str, Any] = {
    "running": False,
    "universe": "",
    "started_at": "",
    "completed_at": "",
    "completed": 0,
    "total": 0,
    "last_symbol": "",
    "message": "No scan has run in this app process yet.",
    "errors": [],
}


INDIAN_STOCK_ALIASES = {
    "RELIANCE": "RELIANCE.NS",
    "RELIANCE INDUSTRIES": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "TATA CONSULTANCY": "TCS.NS",
    "INFY": "INFY.NS",
    "INFOSYS": "INFY.NS",
    "HDFC BANK": "HDFCBANK.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICI BANK": "ICICIBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "SBI": "SBIN.NS",
    "STATE BANK OF INDIA": "SBIN.NS",
    "SBIN": "SBIN.NS",
    "ITC": "ITC.NS",
    "LT": "LT.NS",
    "LARSEN": "LT.NS",
    "LARSEN TOUBRO": "LT.NS",
    "BHARTI AIRTEL": "BHARTIARTL.NS",
    "AIRTEL": "BHARTIARTL.NS",
    "AXIS BANK": "AXISBANK.NS",
    "AXISBANK": "AXISBANK.NS",
    "KOTAK BANK": "KOTAKBANK.NS",
    "KOTAKBANK": "KOTAKBANK.NS",
    "MARUTI": "MARUTI.NS",
    "SUN PHARMA": "SUNPHARMA.NS",
    "SUNPHARMA": "SUNPHARMA.NS",
    "TATA MOTORS": "TATAMOTORS.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "BAJAJ FINANCE": "BAJFINANCE.NS",
    "HUL": "HINDUNILVR.NS",
    "HINDUSTAN UNILEVER": "HINDUNILVR.NS",
    "HINDUNILVR": "HINDUNILVR.NS",
    "ASIAN PAINTS": "ASIANPAINT.NS",
    "ASIANPAINT": "ASIANPAINT.NS",
    "ADANI ENTERPRISES": "ADANIENT.NS",
    "ADANIENT": "ADANIENT.NS",
    "WIPRO": "WIPRO.NS",
    "HCLTECH": "HCLTECH.NS",
    "HCL TECHNOLOGIES": "HCLTECH.NS",
    "ULTRACEMCO": "ULTRACEMCO.NS",
    "ULTRATECH": "ULTRACEMCO.NS",
    "NTPC": "NTPC.NS",
    "POWERGRID": "POWERGRID.NS",
    "POWER GRID": "POWERGRID.NS",
    "ONGC": "ONGC.NS",
    "COAL INDIA": "COALINDIA.NS",
    "COALINDIA": "COALINDIA.NS",
    "TITAN": "TITAN.NS",
    "NESTLE INDIA": "NESTLEIND.NS",
    "NESTLEIND": "NESTLEIND.NS",
    "M&M": "M&M.NS",
    "MAHINDRA": "M&M.NS",
    "MAHINDRA MAHINDRA": "M&M.NS",
    "BAJAJ AUTO": "BAJAJ-AUTO.NS",
    "TECH MAHINDRA": "TECHM.NS",
    "TECHM": "TECHM.NS",
    "JSW STEEL": "JSWSTEEL.NS",
    "JSWSTEEL": "JSWSTEEL.NS",
    "TATA STEEL": "TATASTEEL.NS",
    "TATASTEEL": "TATASTEEL.NS",
    "HINDALCO": "HINDALCO.NS",
    "CIPLA": "CIPLA.NS",
    "DR REDDY": "DRREDDY.NS",
    "DRREDDY": "DRREDDY.NS",
    "DIVIS LAB": "DIVISLAB.NS",
    "DIVISLAB": "DIVISLAB.NS",
    "EICHER": "EICHERMOT.NS",
    "EICHER MOTORS": "EICHERMOT.NS",
    "GRASIM": "GRASIM.NS",
    "HEROMOTOCO": "HEROMOTOCO.NS",
    "HERO MOTOCORP": "HEROMOTOCO.NS",
    "INDUSIND BANK": "INDUSINDBK.NS",
    "INDUSINDBK": "INDUSINDBK.NS",
    "BRITANNIA": "BRITANNIA.NS",
    "APOLLO HOSPITALS": "APOLLOHOSP.NS",
    "APOLLOHOSP": "APOLLOHOSP.NS",
}


SECTOR_CYCLICALITY = {
    "Financial Services": 0.62,
    "Basic Materials": 0.72,
    "Consumer Cyclical": 0.68,
    "Energy": 0.70,
    "Industrials": 0.62,
    "Real Estate": 0.72,
    "Technology": 0.58,
    "Communication Services": 0.55,
    "Healthcare": 0.36,
    "Consumer Defensive": 0.32,
    "Utilities": 0.28,
}


POSITIVE_NEWS_WORDS = {
    "beat",
    "beats",
    "upgrade",
    "upgrades",
    "surge",
    "surges",
    "profit",
    "profits",
    "growth",
    "record",
    "launch",
    "expansion",
    "wins",
    "order",
    "raises",
    "dividend",
    "buyback",
    "approval",
    "partnership",
    "deal",
    "rally",
    "outperform",
}


NEGATIVE_NEWS_WORDS = {
    "miss",
    "misses",
    "downgrade",
    "downgrades",
    "falls",
    "fall",
    "decline",
    "declines",
    "loss",
    "losses",
    "probe",
    "fraud",
    "lawsuit",
    "penalty",
    "weak",
    "slump",
    "slumps",
    "delay",
    "concern",
    "concerns",
    "cut",
    "cuts",
    "warning",
    "debt",
}


@dataclass
class ScoreBlock:
    score: int
    label: str
    summary: str
    sub_scores: Dict[str, Dict[str, Any]]
    bullets: List[str]
    details: Dict[str, Any]


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    if value is None or math.isnan(float(value)):
        return low
    return max(low, min(high, float(value)))


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
            if value in {"", "None", "nan", "NaN", "N/A"}:
                return default
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    number = safe_float(value)
    if number is None:
        return default
    return int(number)


def fmt_money(value: Any, currency: str = "INR") -> str:
    number = safe_float(value)
    if number is None:
        return "Data unavailable"
    abs_number = abs(number)
    if currency.upper() == "INR":
        if abs_number >= 10_000_000:
            return f"INR {number / 10_000_000:,.2f} cr"
        if abs_number >= 100_000:
            return f"INR {number / 100_000:,.2f} lakh"
        return f"INR {number:,.2f}"
    return f"{currency} {number:,.2f}"


def fmt_price(value: Any, currency: str = "INR") -> str:
    number = safe_float(value)
    if number is None:
        return "Data unavailable"
    prefix = "INR" if currency.upper() == "INR" else currency.upper()
    return f"{prefix} {number:,.2f}"


def fmt_large_number(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "Data unavailable"
    abs_number = abs(number)
    if abs_number >= 10_000_000:
        return f"{number / 10_000_000:,.2f} cr"
    if abs_number >= 100_000:
        return f"{number / 100_000:,.2f} lakh"
    return f"{number:,.0f}"


def fmt_pct(value: Any, digits: int = 1, already_percent: bool = False) -> str:
    number = safe_float(value)
    if number is None:
        return "Data unavailable"
    if not already_percent:
        number *= 100
    return f"{number:,.{digits}f}%"


def fmt_ratio(value: Any, digits: int = 2) -> str:
    number = safe_float(value)
    if number is None:
        return "Data unavailable"
    return f"{number:,.{digits}f}x"


def as_inr_debt_to_equity(value: Any) -> Optional[float]:
    number = safe_float(value)
    if number is None:
        return None
    return number / 100 if abs(number) > 5 else number


def score_to_grade(score: float) -> str:
    if score >= 85:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    if score >= 25:
        return "D"
    return "F"


def score_to_signal(score: float) -> str:
    if score >= 85:
        return "Strong Buy"
    if score >= 70:
        return "Buy"
    if score >= 55:
        return "Hold / Accumulate"
    if score >= 40:
        return "Neutral"
    if score >= 25:
        return "Caution"
    return "Avoid"


def score_tone(score: float) -> str:
    if score >= 70:
        return "positive"
    if score >= 55:
        return "balanced"
    if score >= 40:
        return "watch"
    return "negative"


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("-", " ").upper()).strip()


def resolve_candidates(query: str) -> List[str]:
    raw = (query or "").strip()
    if not raw:
        return []
    upper = raw.upper().strip()
    normalized = normalize_key(upper)

    if normalized in INDIAN_STOCK_ALIASES:
        symbol = INDIAN_STOCK_ALIASES[normalized]
        return [symbol, symbol.replace(".NS", ".BO")]

    if upper.endswith((".NS", ".BO")):
        return [upper]

    if re.fullmatch(r"\d{6}", upper):
        return [f"{upper}.BO", f"{upper}.NS"]

    compact = re.sub(r"[^A-Z0-9&]", "", upper)
    if compact in INDIAN_STOCK_ALIASES:
        return [INDIAN_STOCK_ALIASES[compact]]

    candidates = [f"{compact}.NS", f"{compact}.BO"]
    return list(dict.fromkeys([candidate for candidate in candidates if candidate]))


def import_yfinance():
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError(
            "The yfinance package is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return yf


def normalize_history_frame(history: Any) -> pd.DataFrame:
    if history is None:
        return pd.DataFrame()

    frame = pd.DataFrame(history).copy()
    if frame.empty:
        return pd.DataFrame()

    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(col[0]) for col in frame.columns]

    if "Date" not in frame.columns:
        frame = frame.reset_index()

    if "Date" in frame.columns:
        dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
        frame["Date"] = dates.dt.tz_localize(None)

    if "Adj Close" not in frame.columns and "Close" in frame.columns:
        frame["Adj Close"] = frame["Close"]

    required = ["Open", "High", "Low", "Close", "Volume"]
    for column in required:
        if column not in frame.columns:
            frame[column] = np.nan

    frame = frame.dropna(subset=["Close"])
    return frame


def fetch_yahoo_chart_history(symbol: str, days: int = 730) -> pd.DataFrame:
    import requests

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = requests.get(
        url,
        params={
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
            "events": "history",
        },
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            )
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        return pd.DataFrame()

    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    adjclose = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")

    frame = pd.DataFrame(
        {
            "Date": pd.to_datetime(timestamps, unit="s", utc=True).tz_localize(None),
            "Open": quote.get("open"),
            "High": quote.get("high"),
            "Low": quote.get("low"),
            "Close": quote.get("close"),
            "Adj Close": adjclose or quote.get("close"),
            "Volume": quote.get("volume"),
        }
    )
    return normalize_history_frame(frame)


@lru_cache(maxsize=64)
def fetch_symbol_bundle(symbol: str) -> Dict[str, Any]:
    yf = import_yfinance()
    ticker = yf.Ticker(symbol)

    try:
        history = normalize_history_frame(ticker.history(period="2y", interval="1d", auto_adjust=False))
    except Exception:
        history = pd.DataFrame()

    if history.empty:
        try:
            history = normalize_history_frame(
                yf.download(
                    symbol,
                    period="2y",
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
            )
        except Exception:
            history = pd.DataFrame()

    if history.empty:
        try:
            history = fetch_yahoo_chart_history(symbol)
        except Exception:
            history = pd.DataFrame()

    if history.empty:
        raise ValueError(f"No price history returned for {symbol}.")

    info: Dict[str, Any] = {}
    try:
        info = ticker.info or {}
    except Exception:
        info = {}

    news: List[Dict[str, Any]] = []
    try:
        news = ticker.news or []
    except Exception:
        news = []

    calendar: Any = None
    try:
        calendar = ticker.calendar
    except Exception:
        calendar = None

    return {
        "symbol": symbol,
        "history": history,
        "info": info,
        "news": news,
        "calendar": calendar,
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def load_stock_bundle(query: str) -> Dict[str, Any]:
    errors = []
    for symbol in resolve_candidates(query):
        try:
            return fetch_symbol_bundle(symbol)
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
    raise ValueError(
        "Could not find usable NSE/BSE market data for this input. "
        f"Tried: {', '.join(resolve_candidates(query)) or 'no candidates'}. "
        f"Details: {' | '.join(errors) if errors else 'No symbol candidates.'}"
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc_now() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def nifty_symbol(symbol: str) -> str:
    clean = str(symbol or "").strip().upper()
    if not clean:
        return ""
    return clean if clean.endswith((".NS", ".BO")) else f"{clean}.NS"


def is_tradable_nse_symbol(symbol: str) -> bool:
    clean = str(symbol or "").strip().upper()
    base = clean.split(".", 1)[0]
    if not base or base.startswith("DUMMY"):
        return False
    return bool(re.fullmatch(r"[A-Z0-9&-]+", base))


def normalize_universe(value: str) -> str:
    cleaned = normalize_key(value or DEFAULT_SCAN_UNIVERSE).replace("NIFTY", "NIFTY ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in {"NIFTY 500", "NIFTY500"}:
        return "NIFTY 500"
    return "NIFTY 100"


def scan_cache_file(universe: str = DEFAULT_SCAN_UNIVERSE) -> Path:
    normalized = normalize_universe(universe)
    slug = INDEX_UNIVERSES[normalized]["slug"]
    return SCAN_CACHE_DIR / f"{slug}-top-picks.json"


def load_env_index_symbols(env_var: str) -> List[str]:
    raw = os.getenv(env_var, "")
    if not raw.strip():
        return []
    symbols = [nifty_symbol(item) for item in re.split(r"[\s,]+", raw) if item.strip()]
    return list(dict.fromkeys(symbol for symbol in symbols if is_tradable_nse_symbol(symbol)))


@lru_cache(maxsize=4)
def load_index_symbols(universe: str = DEFAULT_SCAN_UNIVERSE) -> Tuple[List[str], str]:
    normalized = normalize_universe(universe)
    config = INDEX_UNIVERSES[normalized]
    env_symbols = load_env_index_symbols(str(config["env_var"]))
    if env_symbols:
        return env_symbols, f"{config['env_var']} environment variable"

    try:
        import requests

        response = requests.get(
            str(config["csv_url"]),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                ),
                "Accept": "text/csv,application/csv,text/plain,*/*",
                "Referer": "https://www.niftyindices.com/",
            },
            timeout=20,
        )
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text))
        symbol_columns = [column for column in frame.columns if str(column).strip().lower() == "symbol"]
        if symbol_columns:
            symbols = [nifty_symbol(item) for item in frame[symbol_columns[0]].dropna().tolist()]
            symbols = list(dict.fromkeys(symbol for symbol in symbols if is_tradable_nse_symbol(symbol)))
            if len(symbols) >= int(config["min_symbols"]):
                return symbols, f"official Nifty Indices {normalized} constituent CSV"
    except Exception:
        pass

    fallback = list(config["fallback_symbols"])
    if fallback:
        return fallback, f"bundled {normalized} fallback list"

    raise RuntimeError(
        f"Could not load {normalized} symbols from the official Nifty Indices CSV. "
        f"Set {config['env_var']} with comma-separated NSE tickers as an override."
    )


def load_nifty100_symbols() -> Tuple[List[str], str]:
    return load_index_symbols("NIFTY 100")


def load_scan_cache(universe: str = DEFAULT_SCAN_UNIVERSE) -> Optional[Dict[str, Any]]:
    try:
        cache_file = scan_cache_file(universe)
        if not cache_file.exists():
            return None
        with cache_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def save_scan_cache(payload: Dict[str, Any], universe: str = DEFAULT_SCAN_UNIVERSE) -> None:
    SCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = scan_cache_file(universe)
    temporary = cache_file.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    temporary.replace(cache_file)


def cache_age_hours(cache: Optional[Dict[str, Any]]) -> Optional[float]:
    if not cache:
        return None
    created_at = parse_utc_iso(str(cache.get("created_at", "")))
    if not created_at:
        return None
    return max(0.0, (utc_now() - created_at).total_seconds() / 3600)


def is_scan_cache_fresh(cache: Optional[Dict[str, Any]]) -> bool:
    age = cache_age_hours(cache)
    return age is not None and age <= SCAN_CACHE_TTL_HOURS


def update_scan_state(**kwargs: Any) -> None:
    with SCAN_LOCK:
        SCAN_STATE.update(kwargs)


def current_scan_state() -> Dict[str, Any]:
    with SCAN_LOCK:
        return dict(SCAN_STATE)


def summarize_report_for_scan(report: Dict[str, Any]) -> Dict[str, Any]:
    technical = report["technical"]
    risk = report["risk"]
    thesis = report["thesis"]
    return {
        "symbol": report["symbol"],
        "company_name": report["company_name"],
        "sector": report["sector"],
        "industry": report["industry"],
        "price": round(float(report["price"]), 2),
        "composite": int(report["composite"]),
        "grade": report["grade"],
        "signal": report["signal"],
        "dimension_scores": report["dimension_scores"],
        "entry_zone": [round(float(value), 2) for value in thesis.details["entry_zone"]],
        "stop_loss": round(float(thesis.details["stop_loss"]), 2),
        "target_1": round(float(thesis.details["target_1"]), 2),
        "risk_reward": round(float(technical.details.get("risk_reward") or 0), 2),
        "one_month_return": technical.details["returns"].get("1M"),
        "risk_label": risk.label,
        "report_generated_at": report["retrieved_at"],
    }


def run_index_scan(universe: str = DEFAULT_SCAN_UNIVERSE, force: bool = False) -> None:
    normalized = normalize_universe(universe)
    cache = load_scan_cache(normalized)
    if cache and is_scan_cache_fresh(cache) and not force:
        update_scan_state(
            running=False,
            universe=normalized,
            completed=len(cache.get("results", [])),
            total=cache.get("total_symbols", 0),
            completed_at=cache.get("created_at", ""),
            message=f"Fresh {normalized} cache is already available.",
        )
        return

    try:
        symbols, source = load_index_symbols(normalized)
    except Exception as exc:
        update_scan_state(
            running=False,
            universe=normalized,
            completed=0,
            total=0,
            completed_at=iso_utc_now(),
            last_symbol="",
            message=str(exc),
            errors=[{"symbol": normalized, "error": str(exc)}],
        )
        return

    started_at = iso_utc_now()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    update_scan_state(
        running=True,
        universe=normalized,
        started_at=started_at,
        completed_at="",
        completed=0,
        total=len(symbols),
        last_symbol="",
        message=f"Scanning {normalized} with the same scoring model used for full reports.",
        errors=[],
    )

    for index, symbol in enumerate(symbols, start=1):
        update_scan_state(last_symbol=symbol, completed=index - 1)
        try:
            report = build_report(symbol)
            results.append(summarize_report_for_scan(report))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            update_scan_state(errors=errors[-8:])
        update_scan_state(completed=index)

    results.sort(key=lambda item: item["composite"], reverse=True)
    payload = {
        "universe": normalized,
        "source": source,
        "created_at": iso_utc_now(),
        "started_at": started_at,
        "total_symbols": len(symbols),
        "completed": len(results),
        "failed": errors,
        "results": results,
        "disclaimer": DISCLAIMER,
    }
    save_scan_cache(payload, normalized)
    update_scan_state(
        running=False,
        universe=normalized,
        completed=len(symbols),
        total=len(symbols),
        completed_at=payload["created_at"],
        last_symbol="",
        message=f"{normalized} scan complete. {len(results)} stocks scored; {len(errors)} failed.",
        errors=errors[-8:],
    )


def run_nifty100_scan(force: bool = False) -> None:
    run_index_scan("NIFTY 100", force=force)


def ensure_scan_started(force: bool = False, universe: str = DEFAULT_SCAN_UNIVERSE) -> bool:
    normalized = normalize_universe(universe)
    cache = load_scan_cache(normalized)
    if cache and is_scan_cache_fresh(cache) and not force:
        return False

    with SCAN_LOCK:
        if SCAN_STATE.get("running"):
            return False
        SCAN_STATE.update(
            {
                "running": True,
                "universe": normalized,
                "started_at": iso_utc_now(),
                "completed_at": "",
                "completed": 0,
                "total": 0,
                "last_symbol": "",
                "message": f"Starting {normalized} scan.",
                "errors": [],
            }
        )

    thread = threading.Thread(target=run_index_scan, kwargs={"universe": normalized, "force": force}, daemon=True)
    thread.start()
    return True


def refresh_token_is_valid() -> bool:
    configured = os.getenv("CACHE_REFRESH_TOKEN", "").strip()
    if not configured:
        return True
    supplied = request.args.get("token", "").strip()
    header = request.headers.get("Authorization", "")
    bearer = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    return supplied == configured or bearer == configured


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_macd(close: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal
    return macd, signal, histogram


def compute_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean()


def pct_return(close: pd.Series, periods: int) -> Optional[float]:
    if len(close) <= periods:
        return None
    start = safe_float(close.iloc[-periods - 1])
    end = safe_float(close.iloc[-1])
    if not start or end is None:
        return None
    return (end / start) - 1


def annualized_volatility(close: pd.Series, lookback: int = 90) -> Optional[float]:
    returns = close.pct_change().dropna().tail(lookback)
    if len(returns) < 20:
        return None
    return float(returns.std() * math.sqrt(252))


def max_drawdown(close: pd.Series) -> Optional[float]:
    if close.empty:
        return None
    cumulative_high = close.cummax()
    drawdown = close / cumulative_high - 1
    return float(drawdown.min())


def unique_levels(levels: Iterable[Optional[float]], price: float, side: str) -> List[float]:
    cleaned = []
    for level in levels:
        number = safe_float(level)
        if number is None or number <= 0:
            continue
        if side == "support" and number >= price:
            continue
        if side == "resistance" and number <= price:
            continue
        if all(abs(number - existing) / price > 0.015 for existing in cleaned):
            cleaned.append(number)
    reverse = side == "support"
    return sorted(cleaned, reverse=reverse)[:3]


def infer_next_earnings(calendar: Any) -> str:
    if calendar is None:
        return "Data unavailable"
    try:
        if isinstance(calendar, pd.DataFrame) and not calendar.empty:
            for value in calendar.values.flatten():
                if pd.notna(value):
                    return str(pd.to_datetime(value).date())
        if isinstance(calendar, dict):
            for value in calendar.values():
                if isinstance(value, (list, tuple)) and value:
                    return str(pd.to_datetime(value[0]).date())
                if value:
                    return str(pd.to_datetime(value).date())
    except Exception:
        return "Data unavailable"
    return "Data unavailable"


def extract_news_items(news: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for raw in news[:8]:
        content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        title = raw.get("title") or content.get("title") or ""
        publisher = raw.get("publisher") or content.get("provider", {}).get("displayName") or ""
        link = raw.get("link") or content.get("canonicalUrl", {}).get("url") or ""
        published = raw.get("providerPublishTime") or content.get("pubDate") or ""
        if isinstance(published, (int, float)):
            published = datetime.fromtimestamp(published, tz=timezone.utc).strftime("%Y-%m-%d")
        items.append(
            {
                "title": str(title),
                "publisher": str(publisher),
                "link": str(link),
                "published": str(published)[:10],
                "tone": classify_news_title(str(title)),
            }
        )
    return [item for item in items if item["title"]]


def classify_news_title(title: str) -> str:
    words = set(re.findall(r"[a-z]+", title.lower()))
    positive = len(words & POSITIVE_NEWS_WORDS)
    negative = len(words & NEGATIVE_NEWS_WORDS)
    if positive > negative:
        return "Positive"
    if negative > positive:
        return "Negative"
    return "Neutral"


def sub_score(score: float, maximum: int, assessment: str) -> Dict[str, Any]:
    return {
        "score": int(round(clamp(score, 0, maximum))),
        "max": maximum,
        "assessment": assessment,
    }


def analyze_technical(history: pd.DataFrame, info: Dict[str, Any]) -> ScoreBlock:
    frame = history.copy()
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    volume = frame["Volume"].fillna(0).astype(float)
    price = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) > 1 else price

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rsi = compute_rsi(close)
    macd, macd_signal, macd_hist = compute_macd(close)
    atr = compute_atr(frame)

    ret_1m = pct_return(close, 21)
    ret_3m = pct_return(close, 63)
    ret_6m = pct_return(close, 126)
    ret_1y = pct_return(close, 252)
    realized_volatility = annualized_volatility(close, 90)
    drawdown = max_drawdown(close)
    high_52 = float(close.tail(252).max()) if len(close) else price
    low_52 = float(close.tail(252).min()) if len(close) else price

    supports = unique_levels(
        [
            low.tail(20).min(),
            low.tail(50).min(),
            sma50.iloc[-1] if pd.notna(sma50.iloc[-1]) else None,
            sma200.iloc[-1] if pd.notna(sma200.iloc[-1]) else None,
            low_52,
            price - (safe_float(atr.iloc[-1]) or price * 0.03) * 1.5,
        ],
        price,
        "support",
    )
    resistances = unique_levels(
        [
            high.tail(20).max(),
            high.tail(50).max(),
            high_52,
            price + (safe_float(atr.iloc[-1]) or price * 0.03) * 2,
            price * 1.08,
        ],
        price,
        "resistance",
    )

    latest_rsi = safe_float(rsi.iloc[-1], 50) or 50
    latest_macd = safe_float(macd.iloc[-1], 0) or 0
    latest_signal = safe_float(macd_signal.iloc[-1], 0) or 0
    latest_hist = safe_float(macd_hist.iloc[-1], 0) or 0
    prior_hist = safe_float(macd_hist.iloc[-5], latest_hist) if len(macd_hist) >= 5 else latest_hist
    atr_value = safe_float(atr.iloc[-1])
    atr_pct = atr_value / price if atr_value else None
    avg_vol20 = safe_float(volume.tail(20).mean(), 0) or 0
    avg_vol50 = safe_float(volume.tail(50).mean(), 0) or 0
    latest_volume = safe_float(volume.iloc[-1], 0) or 0
    volume_ratio = latest_volume / avg_vol20 if avg_vol20 else None

    ema20_now = safe_float(ema20.iloc[-1], price) or price
    ema50_now = safe_float(ema50.iloc[-1], price) or price
    sma200_now = safe_float(sma200.iloc[-1], price) or price
    sma50_now = safe_float(sma50.iloc[-1], price) or price
    sma50_past = safe_float(sma50.iloc[-20], sma50_now) if len(sma50) > 20 else sma50_now

    trend_score = 0
    trend_score += 4 if price > ema20_now else 1
    trend_score += 4 if price > ema50_now else 1
    trend_score += 4 if price > sma200_now else 1
    trend_score += 4 if ema20_now > ema50_now else 1
    trend_score += 2 if ema50_now > sma200_now else 0
    trend_score += 2 if sma50_now >= sma50_past else 0

    momentum_score = 0
    if 50 <= latest_rsi <= 68:
        momentum_score += 7
    elif 40 <= latest_rsi < 50 or 68 < latest_rsi <= 75:
        momentum_score += 5
    elif 30 <= latest_rsi < 40 or 75 < latest_rsi <= 82:
        momentum_score += 3
    else:
        momentum_score += 1
    momentum_score += 5 if latest_macd > latest_signal else 1
    momentum_score += 3 if latest_hist > prior_hist else 1
    momentum_score += 3 if (ret_1m or 0) > 0 else 1
    momentum_score += 2 if (ret_3m or 0) > 0 else 0

    volume_score = 9
    day_positive = price >= previous
    if volume_ratio is not None:
        if volume_ratio >= 1.4 and day_positive:
            volume_score += 5
        elif volume_ratio >= 1.0:
            volume_score += 3
        elif volume_ratio < 0.55:
            volume_score -= 2
    if avg_vol20 and avg_vol50 and avg_vol20 >= avg_vol50:
        volume_score += 3
    if (ret_1m or 0) > 0 and volume_ratio and volume_ratio >= 0.8:
        volume_score += 3
    if latest_volume * price > 1_000_000_000:
        volume_score += 2

    nearest_support = supports[0] if supports else price * 0.92
    nearest_resistance = resistances[0] if resistances else price * 1.10
    support_distance = (price - nearest_support) / price
    resistance_distance = (nearest_resistance - price) / price
    risk_reward = resistance_distance / support_distance if support_distance > 0 else 1

    pattern_score = 7
    if price > sma200_now:
        pattern_score += 3
    if support_distance <= 0.08:
        pattern_score += 4
    if high_52 and price / high_52 >= 0.92:
        pattern_score += 3
    if risk_reward >= 1.8:
        pattern_score += 3
    if (drawdown or -1) > -0.35:
        pattern_score += 2

    relative_score = 10
    nifty_1m, nifty_3m, nifty_6m = None, None, None
    try:
        nifty = fetch_symbol_bundle("^NSEI")["history"]["Close"].astype(float)
        nifty_1m = pct_return(nifty, 21)
        nifty_3m = pct_return(nifty, 63)
        nifty_6m = pct_return(nifty, 126)
    except Exception:
        pass
    for stock_ret, index_ret in [(ret_1m, nifty_1m), (ret_3m, nifty_3m), (ret_6m, nifty_6m)]:
        if stock_ret is None or index_ret is None:
            continue
        relative_score += 3 if stock_ret > index_ret else -2
    if (ret_1y or 0) > 0:
        relative_score += 2

    sub_scores = {
        "Trend": sub_score(trend_score, 20, "Moving-average alignment and slope."),
        "Momentum": sub_score(momentum_score, 20, f"RSI {latest_rsi:.1f}, MACD {'above' if latest_macd > latest_signal else 'below'} signal."),
        "Volume": sub_score(volume_score, 20, f"Latest volume is {volume_ratio:.2f}x the 20-day average." if volume_ratio else "Volume history is limited."),
        "Pattern": sub_score(pattern_score, 20, f"Nearest support is {fmt_price(nearest_support)} and resistance is {fmt_price(nearest_resistance)}."),
        "Relative Strength": sub_score(relative_score, 20, "Performance compared with Nifty 50 where data is available."),
    }
    score = sum(item["score"] for item in sub_scores.values())

    if score >= 70:
        label = "Bullish"
    elif score >= 50:
        label = "Neutral"
    else:
        label = "Bearish"

    bullets = [
        f"Price is {fmt_price(price)} versus EMA20 {fmt_price(ema20_now)}, EMA50 {fmt_price(ema50_now)}, and SMA200 {fmt_price(sma200_now)}.",
        f"Momentum reads RSI {latest_rsi:.1f}; MACD is {'confirming' if latest_macd > latest_signal else 'not confirming'} the trend.",
        f"One-month return is {fmt_pct(ret_1m)} and six-month return is {fmt_pct(ret_6m)}.",
        f"ATR is {fmt_pct(atr_pct)} of price, useful for stop placement and position sizing." if atr_pct else "ATR data is unavailable, so stop placement is more approximate.",
    ]

    details = {
        "price": price,
        "previous_close": previous,
        "day_change": price - previous,
        "day_change_pct": (price / previous - 1) if previous else None,
        "ema20": ema20_now,
        "ema50": ema50_now,
        "sma200": sma200_now,
        "rsi": latest_rsi,
        "macd": latest_macd,
        "macd_signal": latest_signal,
        "atr": atr_value,
        "atr_pct": atr_pct,
        "volume_ratio": volume_ratio,
        "avg_volume_20": avg_vol20,
        "avg_volume_50": avg_vol50,
        "returns": {
            "1M": ret_1m,
            "3M": ret_3m,
            "6M": ret_6m,
            "1Y": ret_1y,
        },
        "annualized_volatility": realized_volatility,
        "support": supports,
        "resistance": resistances,
        "risk_reward": risk_reward,
        "max_drawdown": drawdown,
        "high_52": high_52,
        "low_52": low_52,
    }
    summary = (
        f"Technical setup is {label.lower()} with a {score}/100 score. "
        f"The model sees risk/reward near {risk_reward:.1f}:1 based on nearest support and resistance."
    )
    return ScoreBlock(score=score, label=label, summary=summary, sub_scores=sub_scores, bullets=bullets, details=details)


def analyze_fundamental(info: Dict[str, Any], technical: ScoreBlock) -> ScoreBlock:
    pe = safe_float(info.get("trailingPE")) or safe_float(info.get("forwardPE"))
    forward_pe = safe_float(info.get("forwardPE"))
    pb = safe_float(info.get("priceToBook"))
    peg = safe_float(info.get("pegRatio"))
    ev_ebitda = safe_float(info.get("enterpriseToEbitda"))
    revenue_growth = safe_float(info.get("revenueGrowth"))
    earnings_growth = safe_float(info.get("earningsGrowth"))
    gross_margin = safe_float(info.get("grossMargins"))
    operating_margin = safe_float(info.get("operatingMargins"))
    net_margin = safe_float(info.get("profitMargins"))
    roe = safe_float(info.get("returnOnEquity"))
    fcf = safe_float(info.get("freeCashflow"))
    debt_to_equity = as_inr_debt_to_equity(info.get("debtToEquity"))
    current_ratio = safe_float(info.get("currentRatio"))
    total_cash = safe_float(info.get("totalCash"))
    total_debt = safe_float(info.get("totalDebt"))
    market_cap = safe_float(info.get("marketCap"))
    sector = info.get("sector") or "Data unavailable"
    dividend_yield = safe_float(info.get("dividendYield"))

    valuation_score = 10
    if pe is not None and pe > 0:
        if pe < 15:
            valuation_score += 7
        elif pe < 25:
            valuation_score += 5
        elif pe < 40:
            valuation_score += 3
        elif pe < 65:
            valuation_score += 1
        else:
            valuation_score -= 4
    elif pe is not None and pe <= 0:
        valuation_score -= 5
    if pb is not None:
        valuation_score += 3 if pb < 3 else 1 if pb < 7 else -2
    if ev_ebitda is not None:
        valuation_score += 3 if ev_ebitda < 14 else 1 if ev_ebitda < 25 else -2
    if peg is not None and peg > 0:
        valuation_score += 2 if peg < 1.5 else -1 if peg > 3 else 0

    growth_score = 8
    if revenue_growth is not None:
        growth_score += 7 if revenue_growth > 0.20 else 5 if revenue_growth > 0.10 else 3 if revenue_growth > 0.03 else -2 if revenue_growth < 0 else 1
    if earnings_growth is not None:
        growth_score += 7 if earnings_growth > 0.20 else 5 if earnings_growth > 0.10 else 3 if earnings_growth > 0.03 else -3 if earnings_growth < 0 else 1
    if (technical.details["returns"].get("1Y") or 0) > 0.10:
        growth_score += 2

    profitability_score = 6
    if gross_margin is not None:
        profitability_score += 3 if gross_margin > 0.45 else 2 if gross_margin > 0.25 else 1 if gross_margin > 0.10 else 0
    if operating_margin is not None:
        profitability_score += 5 if operating_margin > 0.25 else 4 if operating_margin > 0.15 else 2 if operating_margin > 0.07 else -2
    if net_margin is not None:
        profitability_score += 4 if net_margin > 0.18 else 3 if net_margin > 0.10 else 1 if net_margin > 0.03 else -2
    if roe is not None:
        profitability_score += 4 if roe > 0.20 else 3 if roe > 0.12 else 1 if roe > 0.05 else -2
    if fcf is not None:
        profitability_score += 2 if fcf > 0 else -2

    health_score = 8
    if debt_to_equity is not None:
        health_score += 5 if debt_to_equity < 0.4 else 3 if debt_to_equity < 0.9 else 1 if debt_to_equity < 1.5 else -3
    if current_ratio is not None:
        health_score += 4 if current_ratio >= 1.5 else 2 if current_ratio >= 1.0 else -2
    if total_cash is not None and total_debt is not None:
        health_score += 4 if total_cash >= total_debt else 1 if total_cash >= total_debt * 0.5 else -2
    if fcf is not None:
        health_score += 3 if fcf > 0 else -2

    moat_score = 8
    if market_cap is not None:
        moat_score += 4 if market_cap >= 1_000_000_000_000 else 3 if market_cap >= 300_000_000_000 else 1
    if roe is not None and roe > 0.15:
        moat_score += 3
    if operating_margin is not None and operating_margin > 0.15:
        moat_score += 3
    if revenue_growth is not None and revenue_growth > 0.08:
        moat_score += 2
    if sector in {"Consumer Defensive", "Technology", "Healthcare"}:
        moat_score += 1

    sub_scores = {
        "Valuation": sub_score(valuation_score, 20, f"P/E {fmt_ratio(pe)}; P/B {fmt_ratio(pb)}; EV/EBITDA {fmt_ratio(ev_ebitda)}."),
        "Growth": sub_score(growth_score, 20, f"Revenue growth {fmt_pct(revenue_growth)}; earnings growth {fmt_pct(earnings_growth)}."),
        "Profitability": sub_score(profitability_score, 20, f"Operating margin {fmt_pct(operating_margin)}; ROE {fmt_pct(roe)}."),
        "Financial Health": sub_score(health_score, 20, f"Debt/equity {fmt_ratio(debt_to_equity)}; current ratio {fmt_ratio(current_ratio)}."),
        "Moat Strength": sub_score(moat_score, 20, f"Moat proxy uses scale, returns, margins, and sector durability."),
    }
    score = sum(item["score"] for item in sub_scores.values())
    label = "Strong" if score >= 70 else "Adequate" if score >= 50 else "Weak"

    bullets = [
        f"Valuation: trailing/forward P/E is {fmt_ratio(pe)} / {fmt_ratio(forward_pe)} and P/B is {fmt_ratio(pb)}.",
        f"Growth: revenue growth is {fmt_pct(revenue_growth)} and earnings growth is {fmt_pct(earnings_growth)} where reported.",
        f"Profitability: operating margin is {fmt_pct(operating_margin)}, net margin is {fmt_pct(net_margin)}, and ROE is {fmt_pct(roe)}.",
        f"Balance sheet: debt/equity is {fmt_ratio(debt_to_equity)} with current ratio {fmt_ratio(current_ratio)}.",
    ]
    details = {
        "pe": pe,
        "forward_pe": forward_pe,
        "pb": pb,
        "peg": peg,
        "ev_ebitda": ev_ebitda,
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "roe": roe,
        "fcf": fcf,
        "debt_to_equity": debt_to_equity,
        "current_ratio": current_ratio,
        "total_cash": total_cash,
        "total_debt": total_debt,
        "market_cap": market_cap,
        "sector": sector,
        "dividend_yield": dividend_yield,
    }
    summary = (
        f"Fundamentals are {label.lower()} at {score}/100. "
        f"The strongest drivers are {', '.join(top_sub_scores(sub_scores, 2))}."
    )
    return ScoreBlock(score=score, label=label, summary=summary, sub_scores=sub_scores, bullets=bullets, details=details)


def analyze_sentiment(info: Dict[str, Any], news: List[Dict[str, Any]], technical: ScoreBlock) -> ScoreBlock:
    news_items = extract_news_items(news)
    positive_count = sum(1 for item in news_items if item["tone"] == "Positive")
    negative_count = sum(1 for item in news_items if item["tone"] == "Negative")
    neutral_count = sum(1 for item in news_items if item["tone"] == "Neutral")

    if news_items:
        news_score = 10 + (positive_count - negative_count) * 3
        news_assessment = f"{positive_count} positive, {neutral_count} neutral, {negative_count} negative Yahoo Finance headlines."
    else:
        news_score = 10
        news_assessment = "No recent Yahoo Finance headlines were returned; score kept neutral."

    one_month = technical.details["returns"].get("1M")
    volume_ratio = technical.details.get("volume_ratio")
    social_score = 10
    if one_month is not None:
        social_score += 4 if one_month > 0.10 else 2 if one_month > 0.03 else -3 if one_month < -0.08 else 0
    if volume_ratio is not None:
        social_score += 3 if volume_ratio > 1.5 else 1 if volume_ratio > 1.0 else -1 if volume_ratio < 0.5 else 0

    recommendation = info.get("recommendationKey") or info.get("recommendationMean")
    target_mean = safe_float(info.get("targetMeanPrice"))
    price = technical.details["price"]
    target_upside = (target_mean / price - 1) if target_mean and price else None
    analyst_score = 10
    if isinstance(recommendation, str):
        rec = recommendation.lower()
        analyst_score += 6 if "buy" in rec and "strong" in rec else 4 if "buy" in rec else -3 if "sell" in rec else 0
    if target_upside is not None:
        analyst_score += 5 if target_upside > 0.20 else 3 if target_upside > 0.08 else -3 if target_upside < -0.05 else 0

    institutional_pct = safe_float(info.get("heldPercentInstitutions"))
    insider_pct = safe_float(info.get("heldPercentInsiders"))
    inst_score = 10
    if institutional_pct is not None:
        inst_score += 5 if institutional_pct > 0.45 else 3 if institutional_pct > 0.20 else 0
    if info.get("floatShares") and info.get("sharesOutstanding"):
        float_pct = safe_float(info.get("floatShares")) / safe_float(info.get("sharesOutstanding")) if safe_float(info.get("sharesOutstanding")) else None
        if float_pct is not None and float_pct > 0.45:
            inst_score += 2

    shares_short = safe_float(info.get("sharesShort"))
    short_ratio = safe_float(info.get("shortRatio"))
    insider_short_score = 10
    if insider_pct is not None:
        insider_short_score += 3 if 0.02 <= insider_pct <= 0.25 else 1 if insider_pct > 0 else 0
    if short_ratio is not None:
        insider_short_score += 3 if short_ratio < 2 else -3 if short_ratio > 7 else 0
    if shares_short is None and short_ratio is None:
        insider_short_score += 0

    sub_scores = {
        "News Sentiment": sub_score(news_score, 20, news_assessment),
        "Social Buzz Proxy": sub_score(social_score, 20, "Uses one-month return and volume spike as a public-attention proxy."),
        "Analyst Consensus": sub_score(analyst_score, 20, f"Recommendation {recommendation or 'unavailable'}; target upside {fmt_pct(target_upside)}."),
        "Institutional Activity": sub_score(inst_score, 20, f"Institutional holding {fmt_pct(institutional_pct)} where available."),
        "Insider / Short": sub_score(insider_short_score, 20, f"Insider holding {fmt_pct(insider_pct)}; short ratio {fmt_ratio(short_ratio)}."),
    }
    score = sum(item["score"] for item in sub_scores.values())
    label = "Bullish" if score >= 70 else "Neutral" if score >= 50 else "Bearish"

    bullets = [
        news_assessment,
        f"Analyst signal: recommendation is {recommendation or 'data unavailable'} and target upside is {fmt_pct(target_upside)}.",
        f"Momentum proxy: one-month return {fmt_pct(one_month)} with volume at {volume_ratio:.2f}x normal." if volume_ratio else f"Momentum proxy: one-month return {fmt_pct(one_month)}.",
        "For Indian equities, social-media and insider/short data can be sparse in free public feeds; missing data is scored neutrally.",
    ]
    details = {
        "news_items": news_items,
        "positive_news": positive_count,
        "negative_news": negative_count,
        "neutral_news": neutral_count,
        "recommendation": recommendation,
        "target_mean": target_mean,
        "target_upside": target_upside,
        "institutional_pct": institutional_pct,
        "insider_pct": insider_pct,
        "short_ratio": short_ratio,
    }
    summary = (
        f"Sentiment is {label.lower()} at {score}/100. "
        f"The model gives more weight to current headlines, analyst target upside, and volume-confirmed attention."
    )
    return ScoreBlock(score=score, label=label, summary=summary, sub_scores=sub_scores, bullets=bullets, details=details)


def analyze_risk(info: Dict[str, Any], technical: ScoreBlock, fundamental: ScoreBlock) -> ScoreBlock:
    price = technical.details["price"]
    beta = safe_float(info.get("beta"))
    sector = info.get("sector") or "Data unavailable"
    annual_vol = annualized_volatility_from_details(technical)
    atr_pct = technical.details.get("atr_pct")
    drawdown = technical.details.get("max_drawdown")
    avg_volume_20 = technical.details.get("avg_volume_20")
    rupee_volume = (avg_volume_20 or 0) * price
    debt_to_equity = fundamental.details.get("debt_to_equity")

    volatility_score = 20
    if annual_vol is not None:
        volatility_score = 20 if annual_vol < 0.22 else 16 if annual_vol < 0.32 else 12 if annual_vol < 0.45 else 8 if annual_vol < 0.65 else 4
    if beta is not None and beta > 1.3:
        volatility_score -= 3
    if atr_pct is not None and atr_pct > 0.05:
        volatility_score -= 2

    dd = drawdown if drawdown is not None else -0.30
    downside_score = 20 if dd > -0.15 else 16 if dd > -0.25 else 12 if dd > -0.40 else 7 if dd > -0.55 else 3
    if technical.details.get("price", 0) < technical.details.get("sma200", 0):
        downside_score -= 2

    sector_cyc = SECTOR_CYCLICALITY.get(str(sector), 0.50)
    macro_score = 20 - int(sector_cyc * 12)
    if beta is not None:
        macro_score += 2 if beta < 0.8 else -3 if beta > 1.25 else 0
    if debt_to_equity is not None:
        macro_score += 2 if debt_to_equity < 0.5 else -2 if debt_to_equity > 1.2 else 0

    liquidity_score = 3
    if rupee_volume > 5_000_000_000:
        liquidity_score = 20
    elif rupee_volume > 1_000_000_000:
        liquidity_score = 16
    elif rupee_volume > 250_000_000:
        liquidity_score = 12
    elif rupee_volume > 50_000_000:
        liquidity_score = 7

    rr = technical.details.get("risk_reward") or 1
    rr_score = 20 if rr >= 3 else 16 if rr >= 2 else 12 if rr >= 1.3 else 7 if rr >= 0.8 else 3
    if fundamental.score < 45:
        rr_score -= 2

    sub_scores = {
        "Volatility": sub_score(volatility_score, 20, f"Annualized 90-day volatility {fmt_pct(annual_vol)}; beta {fmt_ratio(beta)}."),
        "Downside Protection": sub_score(downside_score, 20, f"Two-year max drawdown {fmt_pct(dd)}."),
        "Macro Resilience": sub_score(macro_score, 20, f"Sector cyclicality proxy for {sector}; debt/equity {fmt_ratio(debt_to_equity)}."),
        "Liquidity": sub_score(liquidity_score, 20, f"20-day average traded value about {fmt_money(rupee_volume)}."),
        "Risk / Reward": sub_score(rr_score, 20, f"Nearest-level risk/reward is about {rr:.1f}:1."),
    }
    score = sum(item["score"] for item in sub_scores.values())
    label = "Low" if score >= 75 else "Moderate" if score >= 55 else "High" if score >= 35 else "Extreme"

    stop_loss = derive_stop_loss(technical)
    entry_mid = technical.details["price"]
    risk_per_share = max(entry_mid - stop_loss, entry_mid * 0.01)
    portfolio_size = 500_000
    shares_for_2pct = int((portfolio_size * 0.02) / risk_per_share) if risk_per_share > 0 else 0

    bullets = [
        f"Risk score is inverted: higher means safer. Current risk level is {label.lower()} at {score}/100.",
        f"Estimated stop level is {fmt_price(stop_loss)}, based on nearby support and ATR.",
        f"At a sample INR 5 lakh portfolio and 2% risk, position size is about {shares_for_2pct} shares before liquidity checks.",
        f"Average traded value is {fmt_money(rupee_volume)}, which drives the liquidity score.",
    ]
    details = {
        "annual_volatility": annual_vol,
        "beta": beta,
        "atr_pct": atr_pct,
        "drawdown": drawdown,
        "rupee_volume": rupee_volume,
        "stop_loss": stop_loss,
        "risk_per_share": risk_per_share,
        "sample_position_shares": shares_for_2pct,
        "risk_level": label,
        "key_risks": build_key_risks(info, technical, fundamental),
    }
    summary = (
        f"Risk profile is {label.lower()} with a {score}/100 safety score. "
        "The main risk drivers are volatility, drawdown history, liquidity, leverage, and current risk/reward."
    )
    return ScoreBlock(score=score, label=label, summary=summary, sub_scores=sub_scores, bullets=bullets, details=details)


def annualized_volatility_from_details(technical: ScoreBlock) -> Optional[float]:
    realized = technical.details.get("annualized_volatility")
    if realized is not None:
        return realized
    # If unavailable here, estimate from ATR as a conservative fallback.
    atr_pct = technical.details.get("atr_pct")
    if atr_pct is None:
        return None
    return min(1.2, atr_pct * math.sqrt(252))


def derive_stop_loss(technical: ScoreBlock) -> float:
    price = technical.details["price"]
    support = technical.details.get("support") or []
    atr = technical.details.get("atr")
    candidates = []
    if support:
        candidates.append(support[0] * 0.985)
    if atr:
        candidates.append(price - 1.5 * atr)
    candidates.append(price * 0.92)
    stop = max([candidate for candidate in candidates if candidate < price], default=price * 0.92)
    return float(stop)


def build_key_risks(info: Dict[str, Any], technical: ScoreBlock, fundamental: ScoreBlock) -> List[Dict[str, str]]:
    risks: List[Dict[str, str]] = []
    sector = info.get("sector") or "sector"
    if (technical.details.get("max_drawdown") or 0) < -0.35:
        risks.append(
            {
                "risk": "Large historical drawdowns",
                "probability": "Medium",
                "impact": "High",
                "mitigation": "Use a defined stop and avoid oversizing.",
            }
        )
    if (fundamental.details.get("debt_to_equity") or 0) > 1.2:
        risks.append(
            {
                "risk": "Elevated leverage",
                "probability": "Medium",
                "impact": "High",
                "mitigation": "Track interest coverage, refinancing news, and cash flow trends.",
            }
        )
    if fundamental.score < 50:
        risks.append(
            {
                "risk": "Weak or mixed fundamentals",
                "probability": "Medium",
                "impact": "Medium",
                "mitigation": "Wait for earnings confirmation or valuation improvement.",
            }
        )
    if technical.score < 50:
        risks.append(
            {
                "risk": "Technical trend is not supportive",
                "probability": "Medium",
                "impact": "Medium",
                "mitigation": "Wait for price to reclaim moving averages with volume.",
            }
        )
    risks.append(
        {
            "risk": f"{sector} macro sensitivity",
            "probability": "Medium",
            "impact": "Medium",
            "mitigation": "Compare performance against Nifty and sector index during market stress.",
        }
    )
    risks.append(
        {
            "risk": "Data-source limitations",
            "probability": "Medium",
            "impact": "Medium",
            "mitigation": "Validate financials, insider data, and corporate actions with NSE/BSE filings.",
        }
    )
    return risks[:5]


def analyze_thesis(
    info: Dict[str, Any],
    technical: ScoreBlock,
    fundamental: ScoreBlock,
    sentiment: ScoreBlock,
    risk: ScoreBlock,
    next_earnings: str,
) -> ScoreBlock:
    catalyst_score = 8
    if next_earnings != "Data unavailable":
        catalyst_score += 4
    if sentiment.details.get("news_items"):
        catalyst_score += 3
    if (fundamental.details.get("revenue_growth") or 0) > 0.08:
        catalyst_score += 3
    if (sentiment.details.get("target_upside") or 0) > 0.10:
        catalyst_score += 2

    timing_score = technical.score / 5
    asymmetry_score = clamp((technical.details.get("risk_reward") or 1) * 6, 4, 20)
    edge_score = (fundamental.score * 0.35 + sentiment.score * 0.25 + technical.score * 0.20 + risk.score * 0.20) / 5
    conviction_score = (technical.score + fundamental.score + sentiment.score + risk.score) / 20

    sub_scores = {
        "Catalyst Clarity": sub_score(catalyst_score, 20, f"Next earnings: {next_earnings}; news/catalyst data included where available."),
        "Timing": sub_score(timing_score, 20, f"Mapped from technical score {technical.score}/100."),
        "Asymmetry": sub_score(asymmetry_score, 20, f"Risk/reward around {technical.details.get('risk_reward', 1):.1f}:1."),
        "Analytical Edge": sub_score(edge_score, 20, "Blend of fundamental quality, sentiment support, technical posture, and risk."),
        "Conviction": sub_score(conviction_score, 20, "Cross-check of all four evidence blocks."),
    }
    score = sum(item["score"] for item in sub_scores.values())
    label = "Strong" if score >= 70 else "Moderate" if score >= 50 else "Weak"

    bull = build_bull_case(technical, fundamental, sentiment)
    bear = build_bear_case(technical, fundamental, sentiment, risk)
    price = technical.details["price"]
    stop = risk.details["stop_loss"]
    resistance = technical.details.get("resistance") or [price * 1.08, price * 1.16]
    target_1 = resistance[0] if resistance else price * 1.08
    target_2 = resistance[1] if len(resistance) > 1 else price * 1.16
    timeframe = "Position trade" if fundamental.score >= 60 else "Swing trade"

    bullets = [
        f"Core thesis is {label.lower()}: composite evidence supports a {timeframe.lower()} setup if price respects risk levels.",
        f"Bull target zone begins near {fmt_price(target_1)}; extended target is {fmt_price(target_2)}.",
        f"Invalidation begins below {fmt_price(stop)} or if the next earnings update weakens the fundamental trend.",
        "The thesis should be reviewed after earnings, major sector news, or a decisive break of support/resistance.",
    ]
    details = {
        "bull_case": bull,
        "bear_case": bear,
        "entry_zone": derive_entry_zone(technical),
        "stop_loss": stop,
        "target_1": target_1,
        "target_2": target_2,
        "timeframe": timeframe,
        "catalysts": build_catalysts(next_earnings, sentiment, fundamental),
    }
    summary = (
        f"Thesis conviction is {label.lower()} at {score}/100. "
        "The decision quality depends on catalyst visibility, timing, asymmetry, and whether the risk controls are acceptable."
    )
    return ScoreBlock(score=score, label=label, summary=summary, sub_scores=sub_scores, bullets=bullets, details=details)


def build_bull_case(technical: ScoreBlock, fundamental: ScoreBlock, sentiment: ScoreBlock) -> List[str]:
    factors = []
    if technical.score >= 60:
        factors.append("Trend and momentum are supportive, with price respecting key moving-average structure.")
    if fundamental.score >= 60:
        factors.append("Fundamental quality is acceptable to strong across growth, profitability, balance sheet, or moat proxies.")
    if sentiment.score >= 60:
        factors.append("Sentiment inputs lean constructive, supported by headlines, analyst target upside, or attention proxies.")
    if (fundamental.details.get("revenue_growth") or 0) > 0.08:
        factors.append("Revenue growth remains a potential catalyst if upcoming results confirm the trajectory.")
    if (technical.details.get("risk_reward") or 0) >= 1.5:
        factors.append("Nearest support and resistance create a favorable enough risk/reward map for a controlled setup.")
    return (factors or ["Bull case depends on renewed momentum, cleaner fundamentals, and confirmation from earnings."])[:5]


def build_bear_case(
    technical: ScoreBlock,
    fundamental: ScoreBlock,
    sentiment: ScoreBlock,
    risk: ScoreBlock,
) -> List[str]:
    factors = []
    if technical.score < 55:
        factors.append("Technical confirmation is incomplete; weak momentum can lead to failed breakouts or sideways action.")
    if fundamental.score < 55:
        factors.append("Fundamental score is mixed, leaving less margin for valuation or earnings disappointment.")
    if sentiment.score < 55:
        factors.append("Sentiment support is limited or mixed, so positive news flow may not be enough to re-rate the stock.")
    if risk.score < 55:
        factors.append("Risk profile is elevated based on volatility, drawdown, liquidity, leverage, or poor asymmetry.")
    factors.append("A broad market sell-off or sector-specific shock can invalidate the setup even if company data is stable.")
    return factors[:5]


def build_catalysts(next_earnings: str, sentiment: ScoreBlock, fundamental: ScoreBlock) -> List[Dict[str, str]]:
    catalysts = []
    catalysts.append(
        {
            "date": next_earnings,
            "event": "Next earnings / results update",
            "impact": "High if management confirms margin, growth, or guidance trend.",
        }
    )
    if sentiment.details.get("news_items"):
        first = sentiment.details["news_items"][0]
        catalysts.append(
            {
                "date": first.get("published") or "Recent",
                "event": first.get("title", "Recent company news"),
                "impact": f"{first.get('tone', 'Neutral')} near-term sentiment input.",
            }
        )
    catalysts.append(
        {
            "date": "Ongoing",
            "event": "Sector and Nifty relative strength",
            "impact": "Supports the thesis if the stock outperforms the broader market.",
        }
    )
    if (fundamental.details.get("dividend_yield") or 0) > 0:
        catalysts.append(
            {
                "date": "Ongoing",
                "event": "Dividend and capital-return profile",
                "impact": f"Dividend yield reported at {fmt_pct(fundamental.details.get('dividend_yield'))}.",
            }
        )
    return catalysts[:4]


def derive_entry_zone(technical: ScoreBlock) -> Tuple[float, float]:
    price = technical.details["price"]
    supports = technical.details.get("support") or []
    atr = technical.details.get("atr") or price * 0.03
    lower = max(supports[0] if supports else price - atr, price - atr * 1.2)
    upper = min(price + atr * 0.4, price * 1.025)
    if lower >= upper:
        lower = price * 0.985
        upper = price * 1.015
    return float(lower), float(upper)


def top_sub_scores(sub_scores: Dict[str, Dict[str, Any]], count: int = 2) -> List[str]:
    ordered = sorted(sub_scores.items(), key=lambda pair: pair[1]["score"], reverse=True)
    return [name for name, _ in ordered[:count]]


def build_report(query: str) -> Dict[str, Any]:
    bundle = load_stock_bundle(query)
    info = bundle["info"]
    history = bundle["history"]
    symbol = bundle["symbol"]

    technical = analyze_technical(history, info)
    fundamental = analyze_fundamental(info, technical)
    sentiment = analyze_sentiment(info, bundle["news"], technical)
    risk = analyze_risk(info, technical, fundamental)
    next_earnings = infer_next_earnings(bundle["calendar"])
    thesis = analyze_thesis(info, technical, fundamental, sentiment, risk, next_earnings)

    dimension_scores = {
        "technical": technical.score,
        "fundamental": fundamental.score,
        "sentiment": sentiment.score,
        "risk": risk.score,
        "thesis": thesis.score,
    }
    composite = round(sum(dimension_scores[key] * WEIGHTS[key] for key in WEIGHTS))
    grade = score_to_grade(composite)
    signal = score_to_signal(composite)
    company_name = info.get("longName") or info.get("shortName") or symbol
    currency = info.get("currency") or "INR"
    price = technical.details["price"]

    executive_summary = [
        (
            f"{company_name} ({symbol}) receives a composite score of {composite}/100, "
            f"which maps to grade {grade} and signal '{signal}'. The score combines "
            "technical strength, fundamental quality, sentiment, risk profile, and thesis conviction using the uploaded trading-rubric weights."
        ),
        (
            f"The current price is {fmt_price(price, currency)}. Technicals are {technical.label.lower()}, "
            f"fundamentals are {fundamental.label.lower()}, sentiment is {sentiment.label.lower()}, "
            f"and the risk layer is {risk.label.lower()}."
        ),
        (
            "This is a structured research output, not a trade instruction. The useful next step is to verify financial statements, corporate filings, and the latest NSE/BSE announcements before acting."
        ),
    ]

    return {
        "query": query,
        "symbol": symbol,
        "company_name": company_name,
        "exchange": info.get("exchange") or "NSE/BSE",
        "sector": info.get("sector") or "Data unavailable",
        "industry": info.get("industry") or "Data unavailable",
        "currency": currency,
        "retrieved_at": bundle["retrieved_at"],
        "next_earnings": next_earnings,
        "price": price,
        "market_cap": safe_float(info.get("marketCap")),
        "avg_volume": technical.details.get("avg_volume_20"),
        "fifty_two_week": (technical.details.get("low_52"), technical.details.get("high_52")),
        "dimension_scores": dimension_scores,
        "composite": composite,
        "grade": grade,
        "signal": signal,
        "tone": score_tone(composite),
        "executive_summary": executive_summary,
        "technical": technical,
        "fundamental": fundamental,
        "sentiment": sentiment,
        "risk": risk,
        "thesis": thesis,
        "disclaimer": DISCLAIMER,
    }


def html_escape(text: Any) -> str:
    return escape(str(text))


def render_subscore_table(block: ScoreBlock) -> str:
    rows = []
    for name, item in block.sub_scores.items():
        pct = item["score"] / item["max"] * 100 if item["max"] else 0
        rows.append(
            f"""
            <tr>
                <td>{html_escape(name)}</td>
                <td><strong>{item['score']}/{item['max']}</strong></td>
                <td>
                    <div class="bar"><span style="width:{pct:.0f}%"></span></div>
                </td>
                <td>{html_escape(item['assessment'])}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def render_bullets(items: Iterable[Any]) -> str:
    return "\n".join(f"<li>{html_escape(item)}</li>" for item in items)


def render_score_cards(report: Dict[str, Any]) -> str:
    labels = {
        "technical": "Technical Strength",
        "fundamental": "Fundamental Quality",
        "sentiment": "Sentiment & Momentum",
        "risk": "Risk Profile",
        "thesis": "Thesis Conviction",
    }
    cards = []
    for key, label in labels.items():
        score = report["dimension_scores"][key]
        cards.append(
            f"""
            <section class="metric">
                <span>{label}</span>
                <strong>{score}</strong>
                <div class="bar"><span style="width:{score}%"></span></div>
                <small>Weight {int(WEIGHTS[key] * 100)}%</small>
            </section>
            """
        )
    return "\n".join(cards)


def render_levels(levels: List[float], currency: str) -> str:
    if not levels:
        return "Data unavailable"
    return ", ".join(fmt_price(level, currency) for level in levels)


def build_html_report(report: Dict[str, Any]) -> str:
    technical = report["technical"]
    fundamental = report["fundamental"]
    sentiment = report["sentiment"]
    risk = report["risk"]
    thesis = report["thesis"]
    currency = report["currency"]
    entry_low, entry_high = thesis.details["entry_zone"]
    target_1 = thesis.details["target_1"]
    target_2 = thesis.details["target_2"]
    stop = thesis.details["stop_loss"]
    rr = (target_1 - ((entry_low + entry_high) / 2)) / max(((entry_low + entry_high) / 2) - stop, 0.01)

    news_rows = ""
    for item in sentiment.details.get("news_items", [])[:5]:
        title = html_escape(item["title"])
        if item.get("link"):
            title = f'<a href="{html_escape(item["link"])}" target="_blank" rel="noreferrer">{title}</a>'
        news_rows += (
            f"<tr><td>{html_escape(item.get('published') or 'Recent')}</td>"
            f"<td>{title}</td><td>{html_escape(item.get('tone', 'Neutral'))}</td></tr>"
        )
    if not news_rows:
        news_rows = "<tr><td colspan='3'>No recent Yahoo Finance headlines returned for this symbol.</td></tr>"

    catalyst_rows = "".join(
        f"<tr><td>{html_escape(row['date'])}</td><td>{html_escape(row['event'])}</td><td>{html_escape(row['impact'])}</td></tr>"
        for row in thesis.details["catalysts"]
    )
    risk_rows = "".join(
        f"<tr><td>{html_escape(row['risk'])}</td><td>{html_escape(row['probability'])}</td><td>{html_escape(row['impact'])}</td><td>{html_escape(row['mitigation'])}</td></tr>"
        for row in risk.details["key_risks"]
    )
    bull_rows = "".join(f"<li>{html_escape(item)}</li>" for item in thesis.details["bull_case"])
    bear_rows = "".join(f"<li>{html_escape(item)}</li>" for item in thesis.details["bear_case"])

    return f"""
    <article class="report">
        <section class="report-page hero-report">
            <div>
                <p class="eyebrow">Generated {html_escape(report['retrieved_at'])}</p>
                <h1>{html_escape(report['company_name'])}</h1>
                <p class="ticker">{html_escape(report['symbol'])} | {html_escape(report['exchange'])} | {html_escape(report['sector'])}</p>
                <p class="summary-line">{html_escape(report['executive_summary'][0])}</p>
            </div>
            <div class="score-dial {html_escape(report['tone'])}">
                <span>{report['composite']}</span>
                <small>/ 100</small>
                <strong>{html_escape(report['grade'])} - {html_escape(report['signal'])}</strong>
            </div>
        </section>

        <section class="report-page">
            <h2>1. Executive Summary</h2>
            {"".join(f"<p>{html_escape(paragraph)}</p>" for paragraph in report["executive_summary"])}
            <div class="metrics-grid">{render_score_cards(report)}</div>
            <table>
                <tbody>
                    <tr><th>Current Price</th><td>{fmt_price(report['price'], currency)}</td><th>Market Cap</th><td>{fmt_money(report['market_cap'], currency)}</td></tr>
                    <tr><th>52-Week Range</th><td>{fmt_price(report['fifty_two_week'][0], currency)} to {fmt_price(report['fifty_two_week'][1], currency)}</td><th>20D Avg Volume</th><td>{fmt_large_number(report['avg_volume'])}</td></tr>
                    <tr><th>Industry</th><td>{html_escape(report['industry'])}</td><th>Next Earnings</th><td>{html_escape(report['next_earnings'])}</td></tr>
                </tbody>
            </table>
        </section>

        <section class="report-page">
            <h2>2. Score Dashboard</h2>
            <p>{html_escape(technical.summary)}</p>
            <p>{html_escape(fundamental.summary)}</p>
            <p>{html_escape(sentiment.summary)}</p>
            <p>{html_escape(risk.summary)}</p>
            <p>{html_escape(thesis.summary)}</p>
            <table>
                <thead><tr><th>Dimension</th><th>Score</th><th>Weight</th><th>Weighted</th></tr></thead>
                <tbody>
                    <tr><td>Technical Strength</td><td>{technical.score}/100</td><td>25%</td><td>{technical.score * 0.25:.1f}</td></tr>
                    <tr><td>Fundamental Quality</td><td>{fundamental.score}/100</td><td>25%</td><td>{fundamental.score * 0.25:.1f}</td></tr>
                    <tr><td>Sentiment & Momentum</td><td>{sentiment.score}/100</td><td>20%</td><td>{sentiment.score * 0.20:.1f}</td></tr>
                    <tr><td>Risk Profile</td><td>{risk.score}/100</td><td>15%</td><td>{risk.score * 0.15:.1f}</td></tr>
                    <tr><td>Thesis Conviction</td><td>{thesis.score}/100</td><td>15%</td><td>{thesis.score * 0.15:.1f}</td></tr>
                    <tr class="total"><td>Composite Trade Score</td><td colspan="2">{html_escape(report['grade'])} - {html_escape(report['signal'])}</td><td>{report['composite']}/100</td></tr>
                </tbody>
            </table>
        </section>

        <section class="report-page">
            <h2>3. Technical Analysis</h2>
            <p>{html_escape(technical.summary)}</p>
            <ul>{render_bullets(technical.bullets)}</ul>
            <table>
                <thead><tr><th>Sub-Dimension</th><th>Score</th><th>Visual</th><th>Assessment</th></tr></thead>
                <tbody>{render_subscore_table(technical)}</tbody>
            </table>
            <table>
                <tbody>
                    <tr><th>Support</th><td>{render_levels(technical.details['support'], currency)}</td></tr>
                    <tr><th>Resistance</th><td>{render_levels(technical.details['resistance'], currency)}</td></tr>
                    <tr><th>RSI / MACD</th><td>RSI {technical.details['rsi']:.1f}; MACD {technical.details['macd']:.2f} vs signal {technical.details['macd_signal']:.2f}</td></tr>
                    <tr><th>Returns</th><td>1M {fmt_pct(technical.details['returns']['1M'])}, 3M {fmt_pct(technical.details['returns']['3M'])}, 6M {fmt_pct(technical.details['returns']['6M'])}, 1Y {fmt_pct(technical.details['returns']['1Y'])}</td></tr>
                </tbody>
            </table>
        </section>

        <section class="report-page">
            <h2>4. Fundamental Analysis</h2>
            <p>{html_escape(fundamental.summary)}</p>
            <ul>{render_bullets(fundamental.bullets)}</ul>
            <table>
                <thead><tr><th>Sub-Dimension</th><th>Score</th><th>Visual</th><th>Assessment</th></tr></thead>
                <tbody>{render_subscore_table(fundamental)}</tbody>
            </table>
            <table>
                <tbody>
                    <tr><th>Revenue Growth</th><td>{fmt_pct(fundamental.details['revenue_growth'])}</td><th>EPS Growth</th><td>{fmt_pct(fundamental.details['earnings_growth'])}</td></tr>
                    <tr><th>Operating Margin</th><td>{fmt_pct(fundamental.details['operating_margin'])}</td><th>Net Margin</th><td>{fmt_pct(fundamental.details['net_margin'])}</td></tr>
                    <tr><th>Free Cash Flow</th><td>{fmt_money(fundamental.details['fcf'], currency)}</td><th>Dividend Yield</th><td>{fmt_pct(fundamental.details['dividend_yield'])}</td></tr>
                </tbody>
            </table>
        </section>

        <section class="report-page">
            <h2>5. Sentiment And Market Narrative</h2>
            <p>{html_escape(sentiment.summary)}</p>
            <ul>{render_bullets(sentiment.bullets)}</ul>
            <table>
                <thead><tr><th>Sub-Dimension</th><th>Score</th><th>Visual</th><th>Assessment</th></tr></thead>
                <tbody>{render_subscore_table(sentiment)}</tbody>
            </table>
            <h3>Recent Headlines</h3>
            <table>
                <thead><tr><th>Date</th><th>Headline</th><th>Tone</th></tr></thead>
                <tbody>{news_rows}</tbody>
            </table>
        </section>

        <section class="report-page">
            <h2>6. Risk Assessment</h2>
            <p>{html_escape(risk.summary)}</p>
            <ul>{render_bullets(risk.bullets)}</ul>
            <table>
                <thead><tr><th>Sub-Dimension</th><th>Score</th><th>Visual</th><th>Assessment</th></tr></thead>
                <tbody>{render_subscore_table(risk)}</tbody>
            </table>
            <h3>Top Risks</h3>
            <table>
                <thead><tr><th>Risk</th><th>Probability</th><th>Impact</th><th>Mitigation</th></tr></thead>
                <tbody>{risk_rows}</tbody>
            </table>
        </section>

        <section class="report-page">
            <h2>7. Investment Thesis</h2>
            <p>{html_escape(thesis.summary)}</p>
            <ul>{render_bullets(thesis.bullets)}</ul>
            <table>
                <thead><tr><th>Sub-Dimension</th><th>Score</th><th>Visual</th><th>Assessment</th></tr></thead>
                <tbody>{render_subscore_table(thesis)}</tbody>
            </table>
            <div class="two-column">
                <section>
                    <h3>Bull Case</h3>
                    <ul>{bull_rows}</ul>
                </section>
                <section>
                    <h3>Bear Case</h3>
                    <ul>{bear_rows}</ul>
                </section>
            </div>
        </section>

        <section class="report-page">
            <h2>8. Entry, Exit, And Catalyst Plan</h2>
            <table>
                <tbody>
                    <tr><th>Entry Zone</th><td>{fmt_price(entry_low, currency)} to {fmt_price(entry_high, currency)}</td></tr>
                    <tr><th>Stop Loss</th><td>{fmt_price(stop, currency)}</td></tr>
                    <tr><th>Target 1</th><td>{fmt_price(target_1, currency)}</td></tr>
                    <tr><th>Target 2</th><td>{fmt_price(target_2, currency)}</td></tr>
                    <tr><th>Risk / Reward</th><td>{rr:.1f}:1 to Target 1</td></tr>
                    <tr><th>Timeframe</th><td>{html_escape(thesis.details['timeframe'])}</td></tr>
                    <tr><th>Sample Position</th><td>{risk.details['sample_position_shares']} shares for a sample INR 5 lakh account at 2% risk</td></tr>
                </tbody>
            </table>
            <h3>Catalyst Calendar</h3>
            <table>
                <thead><tr><th>Date</th><th>Event</th><th>Expected Impact</th></tr></thead>
                <tbody>{catalyst_rows}</tbody>
            </table>
            <p class="disclaimer">{html_escape(report['disclaimer'])}</p>
        </section>
    </article>
    """


def render_top_pick_rows(items: List[Dict[str, Any]], empty_text: str) -> str:
    if not items:
        return f"<tr><td colspan='9'>{html_escape(empty_text)}</td></tr>"

    rows = []
    for rank, item in enumerate(items, start=1):
        dims = item.get("dimension_scores", {})
        entry_zone = item.get("entry_zone") or []
        entry_text = (
            f"{fmt_price(entry_zone[0])} to {fmt_price(entry_zone[1])}"
            if len(entry_zone) == 2
            else "Data unavailable"
        )
        rows.append(
            f"""
            <tr>
                <td><strong>{rank}</strong></td>
                <td>
                    <strong>{html_escape(item.get('company_name', item.get('symbol', '')))}</strong>
                    <small>{html_escape(item.get('symbol', ''))}</small>
                </td>
                <td><span class="score-pill">{safe_int(item.get('composite'))}</span></td>
                <td>{html_escape(item.get('signal', ''))}</td>
                <td>{html_escape(item.get('sector', 'Data unavailable'))}</td>
                <td>{fmt_price(item.get('price'))}</td>
                <td>{fmt_pct(item.get('one_month_return'))}</td>
                <td>
                    <small>Tech {safe_int(dims.get('technical'))} | Fund {safe_int(dims.get('fundamental'))} | Risk {safe_int(dims.get('risk'))}</small>
                    <small>Entry {entry_text}</small>
                </td>
                <td><a class="mini-button" href="/?symbol={quote_plus(str(item.get('symbol', '')))}">View report</a></td>
            </tr>
            """
        )
    return "\n".join(rows)


def build_top_picks_html(
    cache: Optional[Dict[str, Any]],
    state: Dict[str, Any],
    universe: str = DEFAULT_SCAN_UNIVERSE,
    min_score: int = 90,
    limit: int = 5,
) -> str:
    normalized = normalize_universe(universe)
    results = sorted((cache or {}).get("results", []), key=lambda item: item.get("composite", 0), reverse=True)
    qualifying = [item for item in results if safe_int(item.get("composite")) >= min_score][:limit]
    strongest = results[:limit]
    age = cache_age_hours(cache)
    age_text = "No cache yet" if age is None else f"{age:.1f} hours old"
    cache_created = (cache or {}).get("created_at", "Not available")
    source = (cache or {}).get("source", "Not available")
    failures = (cache or {}).get("failed", [])
    running = bool(state.get("running"))
    progress_total = safe_int(state.get("total"))
    progress_done = safe_int(state.get("completed"))
    progress_pct = int((progress_done / progress_total) * 100) if progress_total else 0
    progress_label = (
        f"{progress_done}/{progress_total} symbols scanned"
        if progress_total
        else "Preparing scanner"
    )
    status_class = "running" if running else "ready"
    stale_badge = "" if cache and is_scan_cache_fresh(cache) else "<span class='badge warn'>Refresh due</span>"

    nearest_html = ""
    if not qualifying and strongest:
        nearest_html = f"""
        <section class="report-page">
            <h2>Strongest Available Candidates</h2>
            <p>No cached {normalized} stock is currently above {min_score}/100. These are the five highest scores in the latest scan.</p>
            <table class="pick-table">
                <thead><tr><th>#</th><th>Stock</th><th>Score</th><th>Signal</th><th>Sector</th><th>Price</th><th>1M</th><th>Setup</th><th></th></tr></thead>
                <tbody>{render_top_pick_rows(strongest, "No scan results available yet.")}</tbody>
            </table>
        </section>
        """

    recent_errors = "".join(
        f"<li>{html_escape(item.get('symbol', ''))}: {html_escape(item.get('error', ''))}</li>"
        for item in (state.get("errors") or failures)[-5:]
    )
    if recent_errors:
        recent_errors = f"<details class='scan-errors'><summary>Recent skipped symbols</summary><ul>{recent_errors}</ul></details>"

    return f"""
    <section class="report-page scan-hero">
        <div>
            <p class="eyebrow">{normalized} scanner</p>
            <h2>Top 5 Stocks Above {min_score}</h2>
            <p>The scanner runs the same 100-point technical, fundamental, sentiment, risk, and thesis model across the selected universe, then keeps the result in a separate cache.</p>
            <div class="scan-actions">
                <a class="button secondary" href="/top-picks?refresh=1&universe=nifty500">Use Daily NIFTY 500 Cache</a>
                <a class="button" href="/top-picks?refresh=force&universe=nifty500">Refresh NIFTY 500 Now</a>
                <a class="button quiet" href="/">Analyze One Stock</a>
            </div>
        </div>
        <div class="scan-status {status_class}">
            <strong>{'Scanning' if running else 'Cached'}</strong>
            <span>{html_escape(progress_label if running else age_text)}</span>
            <div class="bar"><span style="width:{progress_pct if running else 100}%"></span></div>
            <small>{html_escape(state.get('message', ''))}</small>
            {stale_badge}
        </div>
    </section>

    <section class="report-page">
        <h2>Qualified Picks</h2>
        <p>Showing up to {limit} stocks from the latest {normalized} scan with composite score of {min_score}/100 or higher. Cache generated: {html_escape(cache_created)}. Universe source: {html_escape(source)}.</p>
        <table class="pick-table">
            <thead><tr><th>#</th><th>Stock</th><th>Score</th><th>Signal</th><th>Sector</th><th>Price</th><th>1M</th><th>Setup</th><th></th></tr></thead>
            <tbody>{render_top_pick_rows(qualifying, "No qualified stocks above this score yet. Start or refresh the scan, then this table will update.")}</tbody>
        </table>
        {recent_errors}
    </section>
    {nearest_html}
    <section class="report-page">
        <h2>Daily Cache Setup</h2>
        <p>For automatic refresh, create a Render Cron Job that calls <code>/refresh-cache</code> once per day. The default cron refresh now uses NIFTY 500 and keeps a separate NIFTY 500 cache.</p>
    </section>
    """


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    {% if auto_refresh %}<meta http-equiv="refresh" content="20">{% endif %}
    <title>Indian Stock Score Dashboard</title>
    <style>
        :root {
            --ink: #17212b;
            --muted: #5d6875;
            --line: #d8dee7;
            --paper: #f6f7f2;
            --panel: #ffffff;
            --green: #16865f;
            --red: #b42335;
            --amber: #b7791f;
            --blue: #2563a6;
            --plum: #654062;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: var(--ink);
            background: var(--paper);
            letter-spacing: 0;
        }
        a { color: var(--blue); }
        .shell { max-width: 1180px; margin: 0 auto; padding: 24px; }
        header.app-head {
            display: grid;
            grid-template-columns: 1fr;
            gap: 16px;
            padding: 18px 0 22px;
        }
        h1, h2, h3, p { margin-top: 0; }
        h1 { font-size: clamp(30px, 5vw, 58px); line-height: 1; margin-bottom: 12px; }
        h2 { font-size: 26px; margin-bottom: 14px; }
        h3 { font-size: 17px; margin-bottom: 10px; }
        p { color: var(--muted); line-height: 1.55; }
        .form-panel {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 12px 40px rgba(23, 33, 43, 0.08);
        }
        form.search {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 10px;
            align-items: center;
        }
        input[type="text"] {
            width: 100%;
            min-height: 46px;
            border: 1px solid var(--line);
            border-radius: 6px;
            padding: 0 14px;
            font-size: 16px;
            background: #fff;
        }
        button, .button {
            min-height: 46px;
            border: 0;
            border-radius: 6px;
            background: var(--ink);
            color: #fff;
            padding: 0 18px;
            font-weight: 700;
            cursor: pointer;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            white-space: nowrap;
        }
        .button.secondary { background: var(--green); }
        .button.quiet {
            background: #eef2f5;
            color: var(--ink);
            border: 1px solid var(--line);
        }
        .hint { margin: 10px 0 0; font-size: 13px; }
        .quick-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
        .chip {
            border: 1px solid var(--line);
            border-radius: 999px;
            padding: 7px 10px;
            background: #fff;
            color: var(--ink);
            text-decoration: none;
            font-size: 13px;
        }
        .error {
            background: #fff3f3;
            border: 1px solid #efb5b5;
            color: #8b1d2c;
            padding: 14px;
            border-radius: 8px;
            margin: 18px 0;
        }
        .actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 18px 0; }
        .scan-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
        .scan-hero {
            display: grid;
            grid-template-columns: minmax(0, 1fr) 280px;
            gap: 20px;
            align-items: center;
            background: linear-gradient(135deg, #ffffff 0%, #eef7f1 52%, #f8f1e5 100%);
        }
        .scan-status {
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 16px;
            background: #fff;
        }
        .scan-status strong, .scan-status span, .scan-status small {
            display: block;
        }
        .scan-status strong { font-size: 22px; }
        .scan-status span { color: var(--muted); margin: 4px 0 12px; }
        .scan-status small { color: var(--muted); margin-top: 10px; line-height: 1.4; }
        .badge {
            display: inline-flex;
            width: fit-content;
            margin-top: 10px;
            border-radius: 999px;
            padding: 5px 8px;
            font-size: 12px;
            font-weight: 800;
            color: #6b4b12;
            background: #fff7df;
            border: 1px solid #ead28a;
        }
        .pick-table td small {
            display: block;
            color: var(--muted);
            line-height: 1.45;
        }
        .score-pill {
            display: inline-flex;
            min-width: 44px;
            justify-content: center;
            border-radius: 999px;
            padding: 6px 9px;
            color: #fff;
            background: var(--green);
            font-weight: 900;
        }
        .mini-button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 34px;
            border-radius: 6px;
            padding: 0 10px;
            background: var(--ink);
            color: #fff;
            text-decoration: none;
            font-size: 12px;
            font-weight: 800;
            white-space: nowrap;
        }
        .scan-errors {
            margin-top: 14px;
            color: var(--muted);
        }
        .report {
            display: grid;
            gap: 18px;
        }
        .report-page {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 24px;
            box-shadow: 0 10px 30px rgba(23, 33, 43, 0.07);
        }
        .hero-report {
            display: grid;
            grid-template-columns: 1fr 220px;
            gap: 24px;
            align-items: center;
            min-height: 320px;
            background: linear-gradient(135deg, #ffffff 0%, #f7f1df 48%, #eaf3ef 100%);
        }
        .eyebrow {
            text-transform: uppercase;
            color: var(--plum);
            font-size: 12px;
            font-weight: 800;
            letter-spacing: 0;
            margin-bottom: 12px;
        }
        .ticker { color: var(--ink); font-weight: 700; }
        .summary-line { max-width: 760px; }
        .score-dial {
            width: 200px;
            height: 200px;
            border-radius: 50%;
            border: 18px solid var(--amber);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: #fff;
            justify-self: end;
            text-align: center;
        }
        .score-dial.positive { border-color: var(--green); }
        .score-dial.negative { border-color: var(--red); }
        .score-dial.balanced { border-color: var(--blue); }
        .score-dial span { font-size: 54px; font-weight: 900; line-height: 1; }
        .score-dial small { color: var(--muted); }
        .score-dial strong { margin-top: 8px; font-size: 13px; padding: 0 16px; }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 10px;
            margin: 18px 0;
        }
        .metric {
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 12px;
            background: #fbfcfd;
            min-height: 126px;
        }
        .metric span, .metric small { display: block; color: var(--muted); font-size: 12px; }
        .metric strong { display: block; font-size: 34px; margin: 8px 0; }
        .bar {
            height: 8px;
            width: 100%;
            border-radius: 999px;
            overflow: hidden;
            background: #e6e9ee;
        }
        .bar span {
            display: block;
            height: 100%;
            background: linear-gradient(90deg, var(--red), var(--amber), var(--green));
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 14px 0;
            font-size: 14px;
        }
        th, td {
            border-bottom: 1px solid var(--line);
            padding: 10px;
            text-align: left;
            vertical-align: top;
        }
        th { color: var(--muted); font-size: 12px; text-transform: uppercase; }
        tr.total td { font-weight: 900; background: #f2f5f4; }
        ul { margin: 0 0 18px 18px; padding: 0; color: var(--muted); line-height: 1.55; }
        .two-column { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .two-column section {
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 16px;
            background: #fbfcfd;
        }
        .disclaimer {
            font-size: 12px;
            color: #6b4b12;
            background: #fff7df;
            border: 1px solid #ead28a;
            border-radius: 8px;
            padding: 12px;
        }
        footer { color: var(--muted); font-size: 12px; padding: 18px 0 36px; }
        @media (max-width: 860px) {
            .shell { padding: 14px; }
            form.search, .hero-report, .two-column, .scan-hero { grid-template-columns: 1fr; }
            .metrics-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .score-dial { justify-self: start; }
            table { display: block; overflow-x: auto; white-space: nowrap; }
        }
        @media print {
            body { background: #fff; }
            .form-panel, .actions, footer, .chips { display: none; }
            .shell { max-width: none; padding: 0; }
            .report { display: block; }
            .report-page {
                min-height: 100vh;
                border: 0;
                border-radius: 0;
                box-shadow: none;
                page-break-after: always;
            }
        }
    </style>
</head>
<body>
    <main class="shell">
        <header class="app-head">
            <div>
                <p class="eyebrow">Indian equity research dashboard</p>
                <h1>Stock Score Report</h1>
                <p>Enter an NSE/BSE stock name or ticker to generate an 8-section score report using technical, fundamental, sentiment, risk, and thesis lenses.</p>
            </div>
            <section class="form-panel">
                <form class="search" method="get" action="/">
                    <input type="text" name="symbol" value="{{ query }}" placeholder="Example: Reliance, TCS, INFY.NS, HDFCBANK" autocomplete="off">
                    <button type="submit">Analyze</button>
                </form>
                <p class="hint">Data source: Yahoo Finance through yfinance. Use NSE tickers with .NS or BSE tickers with .BO when a company name is ambiguous.</p>
                <div class="quick-actions">
                    <a class="button secondary" href="/top-picks?refresh=1&universe=nifty500">Scan NIFTY 500</a>
                </div>
                <div class="chips">
                    <a class="chip" href="/?symbol=RELIANCE">Reliance</a>
                    <a class="chip" href="/?symbol=TCS">TCS</a>
                    <a class="chip" href="/?symbol=INFY">Infosys</a>
                    <a class="chip" href="/?symbol=HDFCBANK">HDFC Bank</a>
                    <a class="chip" href="/?symbol=TATAMOTORS">Tata Motors</a>
                </div>
            </section>
        </header>
        {% if error %}
            <div class="error">{{ error }}</div>
        {% endif %}
        {% if top_picks_html %}
            {{ top_picks_html | safe }}
        {% endif %}
        {% if report_html %}
            <div class="actions">
                <a class="button secondary" href="{{ pdf_url }}">Download PDF</a>
                <button onclick="window.print()">Print Report</button>
            </div>
            {{ report_html | safe }}
        {% endif %}
        <footer>{{ disclaimer }}</footer>
    </main>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index() -> str:
    query = request.args.get("symbol", "").strip()
    error = ""
    report_html = ""
    pdf_url = ""
    if query:
        try:
            report = build_report(query)
            report_html = build_html_report(report)
            pdf_url = url_for("download_pdf", symbol=query)
        except Exception as exc:
            error = str(exc)
    return render_template_string(
        PAGE_TEMPLATE,
        query=query,
        error=error,
        report_html=report_html,
        top_picks_html="",
        pdf_url=pdf_url,
        disclaimer=DISCLAIMER,
        auto_refresh=False,
    )


@app.route("/top-picks", methods=["GET"])
def top_picks() -> str:
    refresh = request.args.get("refresh", "").lower().strip()
    min_score = safe_int(request.args.get("min_score"), 90)
    limit = max(1, min(safe_int(request.args.get("limit"), 5), 20))
    error = ""

    force = refresh in {"force", "true-force"}
    requested_universe = request.args.get("universe", "")
    universe = normalize_universe(requested_universe or (FORCE_REFRESH_SCAN_UNIVERSE if force else DEFAULT_SCAN_UNIVERSE))
    cache = load_scan_cache(universe)
    should_start = force or refresh in {"1", "true", "yes"} or not cache or not is_scan_cache_fresh(cache)
    if should_start:
        ensure_scan_started(force=force, universe=universe)

    cache = load_scan_cache(universe)
    state = current_scan_state()
    try:
        top_picks_html = build_top_picks_html(cache, state, universe=universe, min_score=min_score, limit=limit)
    except Exception as exc:
        top_picks_html = ""
        error = str(exc)

    return render_template_string(
        PAGE_TEMPLATE,
        query="",
        error=error,
        report_html="",
        top_picks_html=top_picks_html,
        pdf_url="",
        disclaimer=DISCLAIMER,
        auto_refresh=bool(state.get("running")),
    )


@app.route("/refresh-cache", methods=["GET", "POST"])
def refresh_cache() -> Response:
    if not refresh_token_is_valid():
        return jsonify({"ok": False, "error": "Invalid or missing CACHE_REFRESH_TOKEN."}), 403

    force = request.args.get("force", "").lower() in {"1", "true", "yes"}
    universe = normalize_universe(request.args.get("universe", DEFAULT_SCAN_UNIVERSE))
    started = ensure_scan_started(force=force, universe=universe)
    return jsonify(
        {
            "ok": True,
            "universe": universe,
            "started": started,
            "state": current_scan_state(),
            "cache_file": str(scan_cache_file(universe)),
        }
    )


@app.route("/download.pdf", methods=["GET"])
def download_pdf() -> Response:
    query = request.args.get("symbol", "").strip()
    if not query:
        return Response("Missing symbol", status=400)
    report = build_report(query)
    pdf = build_pdf(report)
    filename = f"{re.sub(r'[^A-Za-z0-9]+', '-', report['symbol']).strip('-')}-stock-score-report.pdf"
    return send_file(
        io.BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


def para(text: Any, style: Any) -> Any:
    from reportlab.platypus import Paragraph

    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def build_pdf(report: Dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.4 * cm,
        leftMargin=1.4 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="Muted", parent=styles["BodyText"], textColor=colors.HexColor("#5d6875")))
    styles["Title"].textColor = colors.HexColor("#17212b")
    styles["Heading1"].textColor = colors.HexColor("#17212b")
    styles["Heading2"].textColor = colors.HexColor("#17212b")

    story: List[Any] = []

    def heading(title: str) -> None:
        story.append(para(title, styles["Heading1"]))
        story.append(Spacer(1, 8))

    def add_table(rows: List[List[Any]], widths: Optional[List[float]] = None) -> None:
        converted = []
        for row in rows:
            converted.append([cell if hasattr(cell, "wrap") else para(cell, styles["Small"]) for cell in row])
        table = Table(converted, colWidths=widths, repeatRows=1 if len(rows) > 1 else 0)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf1f4")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#17212b")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee7")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 10))

    currency = report["currency"]
    technical = report["technical"]
    fundamental = report["fundamental"]
    sentiment = report["sentiment"]
    risk = report["risk"]
    thesis = report["thesis"]
    entry_low, entry_high = thesis.details["entry_zone"]

    heading(f"{report['company_name']} ({report['symbol']})")
    story.append(para(f"Composite Score: {report['composite']}/100 | Grade {report['grade']} | Signal {report['signal']}", styles["Heading2"]))
    story.append(para(f"Generated: {report['retrieved_at']}", styles["Muted"]))
    for paragraph in report["executive_summary"]:
        story.append(para(paragraph, styles["BodyText"]))
        story.append(Spacer(1, 6))
    add_table(
        [
            ["Metric", "Value", "Metric", "Value"],
            ["Price", fmt_price(report["price"], currency), "Market Cap", fmt_money(report["market_cap"], currency)],
            ["Sector", report["sector"], "Industry", report["industry"]],
            ["52W Range", f"{fmt_price(report['fifty_two_week'][0], currency)} to {fmt_price(report['fifty_two_week'][1], currency)}", "Next Earnings", report["next_earnings"]],
        ]
    )
    story.append(PageBreak())

    heading("2. Score Dashboard")
    add_table(
        [
            ["Dimension", "Score", "Weight", "Weighted"],
            ["Technical Strength", f"{technical.score}/100", "25%", f"{technical.score * 0.25:.1f}"],
            ["Fundamental Quality", f"{fundamental.score}/100", "25%", f"{fundamental.score * 0.25:.1f}"],
            ["Sentiment & Momentum", f"{sentiment.score}/100", "20%", f"{sentiment.score * 0.20:.1f}"],
            ["Risk Profile", f"{risk.score}/100", "15%", f"{risk.score * 0.15:.1f}"],
            ["Thesis Conviction", f"{thesis.score}/100", "15%", f"{thesis.score * 0.15:.1f}"],
            ["Composite", f"{report['composite']}/100", report["grade"], report["signal"]],
        ]
    )
    for block in [technical, fundamental, sentiment, risk, thesis]:
        story.append(para(block.summary, styles["BodyText"]))
    story.append(PageBreak())

    heading("3. Technical Analysis")
    add_block_to_pdf(story, styles, technical)
    add_table(
        [
            ["Level", "Value"],
            ["Support", render_pdf_levels(technical.details["support"], currency)],
            ["Resistance", render_pdf_levels(technical.details["resistance"], currency)],
            ["RSI / MACD", f"RSI {technical.details['rsi']:.1f}; MACD {technical.details['macd']:.2f} vs signal {technical.details['macd_signal']:.2f}"],
            ["Returns", f"1M {fmt_pct(technical.details['returns']['1M'])}; 3M {fmt_pct(technical.details['returns']['3M'])}; 6M {fmt_pct(technical.details['returns']['6M'])}; 1Y {fmt_pct(technical.details['returns']['1Y'])}"],
        ]
    )
    story.append(PageBreak())

    heading("4. Fundamental Analysis")
    add_block_to_pdf(story, styles, fundamental)
    add_table(
        [
            ["Metric", "Value", "Metric", "Value"],
            ["P/E", fmt_ratio(fundamental.details["pe"]), "Forward P/E", fmt_ratio(fundamental.details["forward_pe"])],
            ["Revenue Growth", fmt_pct(fundamental.details["revenue_growth"]), "EPS Growth", fmt_pct(fundamental.details["earnings_growth"])],
            ["Operating Margin", fmt_pct(fundamental.details["operating_margin"]), "ROE", fmt_pct(fundamental.details["roe"])],
            ["Debt/Equity", fmt_ratio(fundamental.details["debt_to_equity"]), "Free Cash Flow", fmt_money(fundamental.details["fcf"], currency)],
        ]
    )
    story.append(PageBreak())

    heading("5. Sentiment And Market Narrative")
    add_block_to_pdf(story, styles, sentiment)
    news_rows = [["Date", "Headline", "Tone"]]
    for item in sentiment.details.get("news_items", [])[:5]:
        news_rows.append([item.get("published") or "Recent", item.get("title", ""), item.get("tone", "Neutral")])
    if len(news_rows) == 1:
        news_rows.append(["Data unavailable", "No recent Yahoo Finance headlines returned.", "Neutral"])
    add_table(news_rows, widths=[2.3 * cm, 12 * cm, 2.2 * cm])
    story.append(PageBreak())

    heading("6. Risk Assessment")
    add_block_to_pdf(story, styles, risk)
    risk_rows = [["Risk", "Probability", "Impact", "Mitigation"]]
    for item in risk.details["key_risks"]:
        risk_rows.append([item["risk"], item["probability"], item["impact"], item["mitigation"]])
    add_table(risk_rows, widths=[4.3 * cm, 2.5 * cm, 2.2 * cm, 7 * cm])
    story.append(PageBreak())

    heading("7. Investment Thesis")
    add_block_to_pdf(story, styles, thesis)
    add_table(
        [
            ["Bull Case", "Bear Case"],
            ["\n".join(thesis.details["bull_case"]), "\n".join(thesis.details["bear_case"])],
        ],
        widths=[8 * cm, 8 * cm],
    )
    story.append(PageBreak())

    heading("8. Entry, Exit, And Catalyst Plan")
    add_table(
        [
            ["Parameter", "Level / Note"],
            ["Entry Zone", f"{fmt_price(entry_low, currency)} to {fmt_price(entry_high, currency)}"],
            ["Stop Loss", fmt_price(thesis.details["stop_loss"], currency)],
            ["Target 1", fmt_price(thesis.details["target_1"], currency)],
            ["Target 2", fmt_price(thesis.details["target_2"], currency)],
            ["Timeframe", thesis.details["timeframe"]],
            ["Sample Position", f"{risk.details['sample_position_shares']} shares for a sample INR 5 lakh account at 2% risk"],
        ]
    )
    catalyst_rows = [["Date", "Event", "Expected Impact"]]
    for item in thesis.details["catalysts"]:
        catalyst_rows.append([item["date"], item["event"], item["impact"]])
    add_table(catalyst_rows, widths=[3 * cm, 6 * cm, 7 * cm])
    story.append(para(DISCLAIMER, styles["Small"]))

    doc.build(story)
    return buffer.getvalue()


def render_pdf_levels(levels: List[float], currency: str) -> str:
    if not levels:
        return "Data unavailable"
    return ", ".join(fmt_price(level, currency) for level in levels)


def add_block_to_pdf(story: List[Any], styles: Dict[str, Any], block: ScoreBlock) -> None:
    from reportlab.lib.units import cm

    story.append(para(block.summary, styles["BodyText"]))
    for bullet in block.bullets:
        story.append(para(f"- {bullet}", styles["BodyText"]))
    rows = [["Sub-Dimension", "Score", "Assessment"]]
    for name, item in block.sub_scores.items():
        rows.append([name, f"{item['score']}/{item['max']}", item["assessment"]])
    from reportlab.lib import colors
    from reportlab.platypus import Spacer, Table, TableStyle

    table = Table(
        [[cell if hasattr(cell, "wrap") else para(cell, styles["Small"]) for cell in row] for row in rows],
        colWidths=[4 * cm, 2 * cm, 10 * cm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf1f4")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(Spacer(1, 8))
    story.append(table)
    story.append(Spacer(1, 10))


def run_self_test() -> None:
    assert resolve_candidates("Reliance")[0] == "RELIANCE.NS"
    assert score_to_grade(86) == "A+"
    assert "Buy" in score_to_signal(73)
    assert fmt_pct(0.123) == "12.3%"
    assert normalize_universe("nifty500") == "NIFTY 500"
    assert is_tradable_nse_symbol("VEDL.NS")
    assert not is_tradable_nse_symbol("DUMMYVEDL3.NS")
    symbols, _ = load_nifty100_symbols()
    assert len(symbols) >= 80
    print("Self-test passed: symbol resolution and score helpers are working.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Indian stock score dashboard")
    parser.add_argument("--self-test", action="store_true", help="Run lightweight local checks and exit")
    parser.add_argument("--refresh-cache", action="store_true", help="Refresh a scanner cache and exit")
    parser.add_argument("--universe", default=DEFAULT_SCAN_UNIVERSE, help="Scanner universe: nifty100 or nifty500")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8050")))
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.self_test:
        run_self_test()
    elif args.refresh_cache:
        run_index_scan(args.universe, force=True)
        print(current_scan_state()["message"])
    else:
        app.run(host=args.host, port=args.port, debug=args.debug)
