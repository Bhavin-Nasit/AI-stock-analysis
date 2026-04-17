from flask import Flask, request, jsonify, Response
import subprocess, sys, os, json, re
import yfinance as yf
import anthropic
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

# ── Indian stock resolver ─────────────────────────────────────────────────────
def resolve_indian_ticker(raw: str) -> str:
    """Add .NS suffix for NSE if not already present. Try .BO (BSE) as fallback."""
    raw = raw.strip().upper()
    if raw.endswith(".NS") or raw.endswith(".BO"):
        return raw
    return raw + ".NS"

def get_stock_data(ticker: str) -> dict:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="1y")

        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            # Try BSE fallback
            base = ticker.replace(".NS", "").replace(".BO", "")
            stock = yf.Ticker(base + ".BO")
            info = stock.info
            hist = stock.history(period="1y")
            ticker = base + ".BO"

        price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
        prev  = info.get("previousClose", price) or price
        chg   = ((price - prev) / prev * 100) if prev else 0

        # Calculate technicals from history
        closes = hist["Close"].tolist() if not hist.empty else []
        volumes = hist["Volume"].tolist() if not hist.empty else []
        sma50  = sum(closes[-50:]) / len(closes[-50:])  if len(closes) >= 50  else None
        sma200 = sum(closes[-200:])/ len(closes[-200:]) if len(closes) >= 200 else None
        high52 = max(closes[-252:]) if len(closes) >= 30 else info.get("fiftyTwoWeekHigh")
        low52  = min(closes[-252:]) if len(closes) >= 30 else info.get("fiftyTwoWeekLow")
        avg_vol_30 = sum(volumes[-30:]) / 30 if len(volumes) >= 30 else None

        # RSI (14-day)
        rsi = None
        if len(closes) >= 15:
            deltas = [closes[i]-closes[i-1] for i in range(1,len(closes))]
            gains  = [d if d>0 else 0 for d in deltas[-14:]]
            losses = [-d if d<0 else 0 for d in deltas[-14:]]
            avg_g  = sum(gains)/14; avg_l = sum(losses)/14
            rs = avg_g/avg_l if avg_l else 100
            rsi = round(100 - 100/(1+rs), 1)

        return {
            "ticker": ticker,
            "name":   info.get("longName") or info.get("shortName", ticker),
            "currency": "INR",
            "price":  price,
            "change_pct": round(chg, 2),
            "market_cap": info.get("marketCap"),
            "pe_ttm":     info.get("trailingPE"),
            "pe_fwd":     info.get("forwardPE"),
            "pb":         info.get("priceToBook"),
            "ps":         info.get("priceToSalesTrailing12Months"),
            "eps":        info.get("trailingEps"),
            "eps_fwd":    info.get("forwardEps"),
            "revenue":    info.get("totalRevenue"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "ebitda":     info.get("ebitda"),
            "profit_margin": info.get("profitMargins"),
            "roe":        info.get("returnOnEquity"),
            "roa":        info.get("returnOnAssets"),
            "debt_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "free_cashflow": info.get("freeCashflow"),
            "operating_cashflow": info.get("operatingCashflow"),
            "52w_high": high52,
            "52w_low":  low52,
            "sma50":    round(sma50, 2) if sma50 else None,
            "sma200":   round(sma200, 2) if sma200 else None,
            "rsi14":    rsi,
            "avg_vol_30d": avg_vol_30,
            "avg_volume": info.get("averageVolume"),
            "beta":     info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
            "dividend_rate":  info.get("dividendRate"),
            "payout_ratio":   info.get("payoutRatio"),
            "sector":   info.get("sector"),
            "industry": info.get("industry"),
            "exchange": info.get("exchange"),
            "employees": info.get("fullTimeEmployees"),
            "description": info.get("longBusinessSummary", ""),
            "recommendation": info.get("recommendationKey"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "target_high": info.get("targetHighPrice"),
            "target_low":  info.get("targetLowPrice"),
            "target_mean": info.get("targetMeanPrice"),
            "price_history_30d": closes[-30:],
            "volume_history_30d": volumes[-30:],
        }
    except Exception as e:
        return {"error": str(e), "ticker": ticker}


def run_agent_script(path: str, ticker: str) -> str:
    full = os.path.join(BASE_DIR, path)
    if not os.path.exists(full):
        return ""
    try:
        r = subprocess.run([sys.executable, full, ticker],
                           capture_output=True, text=True, timeout=60)
        return r.stdout.strip()
    except Exception:
        return ""


def fmt_inr(v):
    if v is None: return "N/A"
    if isinstance(v, float) and (v != v): return "N/A"  # nan
    try:
        v = float(v)
        if abs(v) >= 1e12: return f"₹{v/1e12:.2f}L Cr"
        if abs(v) >= 1e7:  return f"₹{v/1e7:.2f} Cr"
        if abs(v) >= 1e5:  return f"₹{v/1e5:.2f} L"
        return f"₹{v:,.2f}"
    except: return str(v)

def fmt_pct(v):
    if v is None: return "N/A"
    try: return f"{float(v)*100:.1f}%"
    except: return str(v)

def fmt_x(v, decimals=1):
    if v is None: return "N/A"
    try: return f"{float(v):.{decimals}f}x"
    except: return str(v)

def fmt_num(v, decimals=2):
    if v is None: return "N/A"
    try: return f"{float(v):.{decimals}f}"
    except: return str(v)


def generate_analysis_stream(ticker: str):
    def sse(event, data):
        safe = data.replace("\n", "\\n")
        return f"event: {event}\ndata: {safe}\n\n"

    yield sse("status", "📡 Fetching NSE/BSE market data...")
    d = get_stock_data(ticker)

    if "error" in d:
        yield sse("error", f"Could not fetch data for {ticker}. Please check the ticker symbol (e.g. RELIANCE, TCS, INFY).")
        return

    yield sse("stockdata", json.dumps(d))
    yield sse("status", f"✅ Got live data for {d['name']}")

    # Run local agents
    agent_outputs = {}
    for name, path in [
        ("technical", "agents/technical_analyst.py"),
        ("fundamental", "agents/fundamental_analyst.py"),
        ("sentiment", "agents/sentiment_agent.py"),
        ("risk", "agents/risk_agent.py"),
        ("canslim", "skills/canslim_screener.py"),
    ]:
        out = run_agent_script(path, ticker)
        if out:
            agent_outputs[name] = out
            yield sse("status", f"✅ {name.title()} agent completed")

    agent_section = ""
    if agent_outputs:
        agent_section = "\n\n## LOCAL AGENT OUTPUTS (incorporate these findings)\n"
        for name, out in agent_outputs.items():
            agent_section += f"\n### {name.upper()}\n{out}\n"

    # Position in 52w range
    pos52w = "N/A"
    if d["52w_high"] and d["52w_low"] and d["price"]:
        rng = d["52w_high"] - d["52w_low"]
        pos52w = f"{((d['price'] - d['52w_low']) / rng * 100):.0f}% of 52-week range" if rng else "N/A"

    trend = "SIDEWAYS"
    if d["sma50"] and d["sma200"] and d["price"]:
        if d["price"] > d["sma50"] > d["sma200"]: trend = "STRONG UPTREND"
        elif d["price"] > d["sma200"]: trend = "UPTREND"
        elif d["price"] < d["sma50"] < d["sma200"]: trend = "STRONG DOWNTREND"
        elif d["price"] < d["sma200"]: trend = "DOWNTREND"

    yield sse("status", "🤖 Running multi-agent AI analysis...")

    prompt = f"""You are a senior Indian equity research analyst at a top-tier institution like ICICI Securities, Motilal Oswal, or Kotak Securities. 
Produce a detailed, institutional-quality equity research report for **{d['name']} ({ticker})** listed on {d.get('exchange','NSE/BSE')}.
All monetary values MUST be in Indian Rupees (₹). Use Indian number system (Lakhs, Crores).

## RAW MARKET DATA (use all figures accurately)

**Price & Market Data**
- CMP (Current Market Price): ₹{fmt_num(d['price'])}
- Day Change: {fmt_num(d['change_pct'])}%
- Market Cap: {fmt_inr(d['market_cap'])}
- 52-Week High: ₹{fmt_num(d['52w_high'])}  |  52-Week Low: ₹{fmt_num(d['52w_low'])}
- Position in 52W Range: {pos52w}
- Beta: {fmt_num(d['beta'])}

**Valuation Metrics**
- P/E (TTM): {fmt_x(d['pe_ttm'])}
- Forward P/E: {fmt_x(d['pe_fwd'])}
- Price/Book: {fmt_x(d['pb'])}
- Price/Sales: {fmt_x(d['ps'])}
- EPS (TTM): ₹{fmt_num(d['eps'])}
- Forward EPS: ₹{fmt_num(d['eps_fwd'])}

**Financials**
- Revenue (TTM): {fmt_inr(d['revenue'])}
- Revenue Growth (YoY): {fmt_pct(d['revenue_growth'])}
- EBITDA: {fmt_inr(d['ebitda'])}
- Net Profit Margin: {fmt_pct(d['profit_margin'])}
- Earnings Growth: {fmt_pct(d['earnings_growth'])}

**Balance Sheet & Returns**
- ROE: {fmt_pct(d['roe'])}
- ROA: {fmt_pct(d['roa'])}
- Debt/Equity: {fmt_num(d['debt_equity'])}
- Current Ratio: {fmt_num(d['current_ratio'])}
- Free Cash Flow: {fmt_inr(d['free_cashflow'])}
- Operating Cash Flow: {fmt_inr(d['operating_cashflow'])}

**Dividends**
- Dividend Yield: {fmt_pct(d['dividend_yield'])}
- Annual Dividend: ₹{fmt_num(d['dividend_rate'])}
- Payout Ratio: {fmt_pct(d['payout_ratio'])}

**Technical Indicators**
- 50-DMA: ₹{fmt_num(d['sma50'])}
- 200-DMA: ₹{fmt_num(d['sma200'])}
- RSI (14): {fmt_num(d['rsi14'], 1)}
- Trend: {trend}

**Analyst Consensus**
- Target (Mean): ₹{fmt_num(d['target_mean'])}
- Target High: ₹{fmt_num(d['target_high'])}
- Target Low: ₹{fmt_num(d['target_low'])}
- Analyst Count: {d['analyst_count']}
- Consensus: {(d['recommendation'] or 'N/A').upper()}

**Company Profile**
- Sector: {d['sector']}  |  Industry: {d['industry']}
- Employees: {d['employees']}
{agent_section}

---

Now generate a **comprehensive 7-8 page institutional equity research report** with EXACTLY these sections. Be thorough, specific, and data-driven. Use the actual numbers provided. All prices in ₹, all large numbers in Indian system (Cr, L).

---

## EXECUTIVE SUMMARY

Write 3-4 paragraphs covering: investment thesis in one line, company overview, key financial highlights, and your overall stance (BUY/HOLD/SELL). Include your 12-month price target prominently.

---

## SECTION 1: COMPANY OVERVIEW & BUSINESS MODEL

- Business segments and revenue mix
- Competitive positioning and market share
- Key products/services and their growth drivers  
- Management quality and promoter holding context
- Recent strategic initiatives or major developments

---

## SECTION 2: INDUSTRY & MACRO ANALYSIS

- Industry size, growth rate (TAM/SAM)
- India-specific macro tailwinds or headwinds
- Competitive landscape: key peers and market dynamics
- Regulatory environment relevant to this sector
- How current RBI/government policy affects this business

---

## SECTION 3: FINANCIAL ANALYSIS

### Revenue & Profitability
Analyze the revenue trend, margin profile, and earnings quality using the data provided.

### Valuation Analysis  
- Compute: Is P/E of {fmt_x(d['pe_ttm'])} cheap/fair/expensive vs Indian sector peers?
- P/B of {fmt_x(d['pb'])} vs asset-heavy or asset-light peers
- PEG ratio interpretation
- DCF narrative: what growth rates justify current valuation?

### Balance Sheet Assessment
- Debt comfort level given D/E of {fmt_num(d['debt_equity'])}
- Cash flow quality — FCF of {fmt_inr(d['free_cashflow'])}
- Working capital and liquidity position

---

## SECTION 4: TECHNICAL ANALYSIS

- **Trend**: {trend} — detailed interpretation
- **Support Levels**: Calculate 2-3 key support levels below CMP ₹{fmt_num(d['price'])}
- **Resistance Levels**: Calculate 2-3 key resistance levels above CMP
- **Moving Averages**: Price vs 50-DMA (₹{fmt_num(d['sma50'])}) and 200-DMA (₹{fmt_num(d['sma200'])}) — what does this signal?
- **RSI**: {fmt_num(d['rsi14'], 1)} — overbought/oversold/neutral interpretation
- **52W Range**: Stock at {pos52w} — momentum context
- **Volume analysis**: what average volumes suggest about institutional participation
- Chart pattern description (breakout/consolidation/distribution)
- Near-term technical outlook: next 1-3 months

---

## SECTION 5: BULL CASE vs BEAR CASE

### 🟢 BULL CASE (Target: ₹[calculate optimistic target])
List 4-5 specific catalysts with quantified impact where possible:
- Growth catalyst 1 with numbers
- Growth catalyst 2 with numbers
- Sector tailwind
- Valuation re-rating trigger
- Operational leverage

### 🔴 BEAR CASE (Target: ₹[calculate downside target])
List 4-5 specific risks with impact assessment:
- Key risk 1 with downside scenario
- Key risk 2
- Macro/regulatory risk
- Competitive disruption risk
- Execution risk

---

## SECTION 6: RISK MATRIX

Create a structured risk assessment table in markdown format:

| Risk Factor | Likelihood | Impact | Mitigation |
|-------------|-----------|--------|------------|
[Fill 6-8 rows with specific risks for this company]

Overall Risk Rating: [Low / Medium / High / Very High] with justification.

---

## SECTION 7: INVESTMENT VERDICT & RECOMMENDATION

### Verdict: [BUY / ACCUMULATE / HOLD / REDUCE / SELL]

**12-Month Price Target: ₹[specific number]**  
**Upside/Downside from CMP: [%]**

**Target Basis**: [P/E target / DCF / EV/EBITDA — explain which multiple and why]

**Investment Thesis** (2 paragraphs): Summarise the core reason to own or avoid this stock.

**Entry Strategy**:
- Ideal buying range: ₹[range]
- Stop loss: ₹[level] ([% below CMP])
- Add more on dips to: ₹[level]

**Position Sizing**: 
- Suitable for: [Conservative / Moderate / Aggressive investors]
- Suggested portfolio weight: [X%]
- Time horizon: [Short (3-6m) / Medium (6-18m) / Long (2-5y)]

**Key Monitorables**: 3-4 specific metrics or events to watch each quarter.

---

## SECTION 8: PEER COMPARISON

Compare {d['name']} with 3-4 key Indian listed peers on:
- P/E, P/B, EV/EBITDA
- Revenue growth, margin profile
- ROE, Debt/Equity
- Brief qualitative differentiation

Conclude which is the best risk-reward in the peer group currently.

---

*Report generated: {datetime.now().strftime('%d %B %Y, %I:%M %p IST')}*  
*Data source: NSE/BSE via Yahoo Finance*  
*This report is for educational purposes only and not SEBI-registered investment advice.*"""

    yield sse("status", "📝 Writing institutional research report...")
    try:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            for text in stream.text_stream:
                yield sse("chunk", text)
    except Exception as e:
        yield sse("error", f"AI analysis failed: {str(e)}")
        return

    yield sse("done", "Report complete")


HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dalal Street AI — Indian Equity Research</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
<style>
:root {
  --bg:       #0a0c0f;
  --surface:  #111418;
  --surface2: #181c22;
  --border:   #232a35;
  --gold:     #e8b84b;
  --gold2:    #f5d37a;
  --green:    #2ecc71;
  --red:      #e74c3c;
  --blue:     #4a9eff;
  --text:     #e8eaf0;
  --muted:    #5a6478;
}
*{margin:0;padding:0;box-sizing:border-box}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'IBM Plex Sans', sans-serif;
  min-height: 100vh;
}

/* subtle paper grain */
body::after {
  content:'';position:fixed;inset:0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
  pointer-events:none;z-index:0;opacity:0.4;
}

.wrapper{position:relative;z-index:1;max-width:1000px;margin:0 auto;padding:0 24px 80px}

/* ── Header ── */
header {
  padding: 52px 0 36px;
  text-align: center;
  border-bottom: 1px solid var(--border);
  margin-bottom: 40px;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 18px;
}
.brand-icon {
  width: 36px; height: 36px;
  background: linear-gradient(135deg, var(--gold), #c8972a);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
}
.brand-name {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 13px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--gold);
  font-weight: 500;
}

h1 {
  font-family: 'Playfair Display', serif;
  font-size: clamp(2rem, 5vw, 3.4rem);
  font-weight: 900;
  line-height: 1.1;
  letter-spacing: -0.02em;
  color: var(--text);
  margin-bottom: 10px;
}
h1 em { color: var(--gold); font-style: normal; }

.tagline {
  color: var(--muted);
  font-size: 15px;
  font-weight: 300;
}

/* ── Search ── */
.search-wrap { margin-top: 36px; display: flex; flex-direction: column; align-items: center; gap: 14px; }

.search-row {
  display: flex;
  width: 100%;
  max-width: 560px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  transition: border-color .2s, box-shadow .2s;
}
.search-row:focus-within {
  border-color: var(--gold);
  box-shadow: 0 0 0 3px rgba(232,184,75,0.12);
}

.search-row input {
  flex:1; background:transparent; border:none; outline:none;
  padding: 16px 20px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 17px; font-weight: 500;
  letter-spacing: 0.1em;
  color: var(--text); text-transform: uppercase;
}
.search-row input::placeholder { color:var(--muted); font-size:13px; letter-spacing:.03em; text-transform:none; font-family:'IBM Plex Sans',sans-serif; font-weight:300 }

.search-btn {
  background: var(--gold);
  color: #0a0c0f;
  border: none;
  padding: 16px 26px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px; font-weight: 500;
  letter-spacing: 0.12em; text-transform: uppercase;
  cursor: pointer; transition: background .2s;
  display: flex; align-items: center; gap: 8px;
}
.search-btn:hover { background: var(--gold2); }
.search-btn:disabled { background: var(--border); color: var(--muted); cursor: not-allowed; }

.chips { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; }
.chip {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px; font-weight: 500; letter-spacing: .1em;
  padding: 5px 12px;
  border: 1px solid var(--border); border-radius: 20px;
  color: var(--muted); cursor: pointer; transition: all .2s;
}
.chip:hover { border-color: var(--gold); color: var(--gold); background: rgba(232,184,75,.06); }

/* ── Status ── */
#status-bar {
  display: none;
  align-items: center; gap: 10px;
  margin-top: 32px;
  padding: 12px 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px; color: var(--gold); letter-spacing: .04em;
}
.spinner { width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--gold);border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0 }
@keyframes spin{to{transform:rotate(360deg)}}

/* ── Stock Header ── */
#stock-header { display:none; margin-top:36px; }

.stock-meta {
  display: flex; justify-content: space-between; align-items: flex-start;
  flex-wrap: wrap; gap: 16px; margin-bottom: 6px;
}
.stock-name-block {}
.stock-name {
  font-family: 'Playfair Display', serif;
  font-size: 1.7rem; font-weight: 700;
  line-height: 1.2;
}
.badge {
  display: inline-block;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px; letter-spacing: .15em;
  background: rgba(232,184,75,.12);
  border: 1px solid rgba(232,184,75,.3);
  color: var(--gold);
  padding: 3px 9px; border-radius: 4px;
  margin-left: 10px; vertical-align: middle;
}
.sub-meta { font-size: 13px; color: var(--muted); margin-top: 4px; }

.price-block { text-align:right; }
.cmp {
  font-family: 'Playfair Display', serif;
  font-size: 2.1rem; font-weight: 700;
}
.day-chg { font-size: 14px; margin-top: 3px; font-weight:500; }
.up{color:var(--green)} .dn{color:var(--red)}

.divider { border:none; border-top:1px solid var(--border); margin:18px 0; }

.metrics { display:grid; grid-template-columns:repeat(auto-fill,minmax(155px,1fr)); gap:10px; margin-bottom:28px; }
.metric {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px; padding:14px 16px;
  transition: border-color .2s;
}
.metric:hover { border-color: rgba(232,184,75,.3); }
.ml { font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); margin-bottom:5px; }
.mv { font-family:'IBM Plex Mono',monospace; font-size:1rem; font-weight:500; color:var(--text); }

/* ── Section tabs ── */
.section-tabs {
  display: flex; gap: 0;
  border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden; margin-bottom: 0;
}
.tab {
  flex:1; padding:10px 8px;
  font-family:'IBM Plex Mono',monospace; font-size:10px;
  letter-spacing:.08em; text-transform:uppercase;
  text-align:center; color:var(--muted);
  background:var(--surface); border:none;
  cursor:pointer; transition:all .2s;
  border-right: 1px solid var(--border);
}
.tab:last-child{border-right:none}
.tab.active { background:var(--gold); color:#0a0c0f; font-weight:500; }

/* ── Report ── */
#report-section {
  display:none; margin-top:0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top:none;
  border-radius: 0 0 10px 10px;
  overflow: hidden;
}

.report-topbar {
  padding: 14px 24px;
  border-bottom: 1px solid var(--border);
  background: var(--surface2);
  display:flex; align-items:center; justify-content:space-between;
}
.report-label {
  font-family:'IBM Plex Mono',monospace; font-size:11px;
  letter-spacing:.15em; text-transform:uppercase; color:var(--gold);
}
.report-actions { display:flex; gap:10px; align-items:center; }
.copy-btn {
  font-family:'IBM Plex Mono',monospace; font-size:10px; letter-spacing:.08em;
  padding:5px 12px; border:1px solid var(--border); border-radius:5px;
  color:var(--muted); background:transparent; cursor:pointer; transition:all .2s;
}
.copy-btn:hover { border-color:var(--gold); color:var(--gold); }
.report-time { font-family:'IBM Plex Mono',monospace; font-size:10px; color:var(--muted); }

.report-body {
  padding: 32px 36px;
  line-height: 1.8; font-size: 15px; font-weight:300;
}

/* Markdown */
.report-body h2 {
  font-family:'Playfair Display',serif; font-size:1.3rem; font-weight:700;
  color:var(--gold); margin:36px 0 14px;
  padding-bottom:10px; border-bottom:1px solid var(--border);
}
.report-body h2:first-child{margin-top:0}
.report-body h3 {
  font-family:'IBM Plex Sans',sans-serif; font-size:1rem; font-weight:600;
  color:var(--text); margin:22px 0 10px;
}
.report-body p { margin-bottom:14px; color:#c8d0dc; line-height:1.8; }
.report-body ul,.report-body ol { padding-left:22px; margin-bottom:14px; }
.report-body li { margin-bottom:7px; color:#c8d0dc; }
.report-body strong { color:var(--text); font-weight:600; }
.report-body em { color:var(--gold2); font-style:normal; }
.report-body hr { border:none; border-top:1px solid var(--border); margin:28px 0; }
.report-body table {
  width:100%; border-collapse:collapse; margin:16px 0; font-size:13px;
  font-family:'IBM Plex Mono',monospace;
}
.report-body th {
  background:var(--surface2); color:var(--gold);
  padding:9px 14px; text-align:left; font-size:10px;
  letter-spacing:.1em; text-transform:uppercase;
  border:1px solid var(--border);
}
.report-body td { padding:9px 14px; border:1px solid var(--border); color:#c8d0dc; }
.report-body tr:hover td { background:rgba(232,184,75,.03); }
.report-body blockquote {
  border-left:3px solid var(--gold); padding:10px 18px;
  background:rgba(232,184,75,.05); border-radius:0 6px 6px 0; margin:14px 0;
}

.cursor {
  display:inline-block; width:2px; height:1.1em;
  background:var(--gold); margin-left:2px;
  animation:blink 1s step-end infinite; vertical-align:text-bottom;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}

/* ── Error ── */
#error-box {
  display:none; margin-top:24px; padding:14px 20px;
  background:rgba(231,76,60,.08); border:1px solid rgba(231,76,60,.3);
  border-radius:8px; color:var(--red); font-size:14px;
}

/* Progress steps */
.progress-steps {
  display:none; margin-top:24px;
  display:flex; flex-direction:column; gap:6px;
}
.step { display:flex; align-items:center; gap:10px; font-size:13px; color:var(--muted); }
.step.done { color:var(--green); }
.step.active { color:var(--gold); }
.step-icon { font-size:14px; width:20px; text-align:center; flex-shrink:0; }

@media(max-width:600px){
  .metrics{grid-template-columns:repeat(2,1fr)}
  .report-body{padding:20px 16px}
  .stock-meta{flex-direction:column}
  .price-block{text-align:left}
  .section-tabs{display:none}
}
</style>
</head>
<body>
<div class="wrapper">

  <header>
    <div class="brand">
      <div class="brand-icon">₹</div>
      <span class="brand-name">Dalal Street AI</span>
    </div>
    <h1>Indian Equity<br><em>Research Engine</em></h1>
    <p class="tagline">Institutional-grade NSE/BSE analysis. Enter any Indian stock symbol.</p>

    <div class="search-wrap">
      <div class="search-row">
        <input type="text" id="ticker-input"
          placeholder="e.g.  RELIANCE  ·  TCS  ·  INFY  ·  HDFCBANK"
          maxlength="20" autocomplete="off" spellcheck="false">
        <button class="search-btn" id="analyze-btn" onclick="startAnalysis()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
          Analyse
        </button>
      </div>
      <div class="chips">
        <span class="chip" onclick="go('RELIANCE')">RELIANCE</span>
        <span class="chip" onclick="go('TCS')">TCS</span>
        <span class="chip" onclick="go('HDFCBANK')">HDFCBANK</span>
        <span class="chip" onclick="go('INFY')">INFY</span>
        <span class="chip" onclick="go('WIPRO')">WIPRO</span>
        <span class="chip" onclick="go('ICICIBANK')">ICICIBANK</span>
        <span class="chip" onclick="go('BAJFINANCE')">BAJFINANCE</span>
        <span class="chip" onclick="go('TATAMOTORS')">TATAMOTORS</span>
        <span class="chip" onclick="go('ADANIENT')">ADANIENT</span>
      </div>
    </div>
  </header>

  <div id="status-bar">
    <div class="spinner"></div>
    <span id="status-text">Initialising...</span>
  </div>

  <div id="error-box"></div>

  <!-- Stock header -->
  <div id="stock-header">
    <div class="stock-meta">
      <div class="stock-name-block">
        <div class="stock-name" id="s-name">—</div>
        <div class="sub-meta" id="s-meta">—</div>
      </div>
      <div class="price-block">
        <div class="cmp" id="s-price">—</div>
        <div class="day-chg" id="s-chg">—</div>
      </div>
    </div>
    <hr class="divider">
    <div class="metrics" id="metrics"></div>
  </div>

  <!-- Tabs + report -->
  <div class="section-tabs" id="tabs" style="display:none">
    <button class="tab active">Research Report</button>
  </div>
  <div id="report-section">
    <div class="report-topbar">
      <span class="report-label">🏦 Equity Research Report</span>
      <div class="report-actions">
        <button class="copy-btn" onclick="copyReport()">Copy Report</button>
        <span class="report-time" id="report-time">—</span>
      </div>
    </div>
    <div class="report-body" id="report-body"></div>
  </div>

</div>

<script>
const $ = id => document.getElementById(id);

function go(t){ $('ticker-input').value=t; startAnalysis(); }

$('ticker-input').addEventListener('keydown', e => { if(e.key==='Enter') startAnalysis(); });

function fmtINR(v){
  if(v==null||v===''||v==='N/A') return '—';
  v=parseFloat(v);
  if(isNaN(v)) return '—';
  if(Math.abs(v)>=1e12) return '₹'+(v/1e12).toFixed(2)+' L Cr';
  if(Math.abs(v)>=1e7)  return '₹'+(v/1e7).toFixed(2)+' Cr';
  if(Math.abs(v)>=1e5)  return '₹'+(v/1e5).toFixed(2)+' L';
  return '₹'+v.toFixed(2);
}
function fmtX(v,d=1){ if(v==null||isNaN(parseFloat(v))) return '—'; return parseFloat(v).toFixed(d)+'x'; }
function fmtPct(v){ if(v==null||isNaN(parseFloat(v))) return '—'; return (parseFloat(v)*100).toFixed(1)+'%'; }
function fmtN(v,d=2){ if(v==null||isNaN(parseFloat(v))) return '—'; return parseFloat(v).toFixed(d); }

function renderStock(d){
  $('s-name').innerHTML = d.name + `<span class="badge">${d.ticker}</span>`;
  $('s-meta').textContent = [d.sector, d.industry, d.exchange].filter(x=>x&&x!=='N/A').join(' · ');

  $('s-price').textContent = d.price ? '₹'+parseFloat(d.price).toFixed(2) : '—';
  const chg = parseFloat(d.change_pct)||0;
  const cls = chg>=0 ? 'up':'dn', arrow = chg>=0?'▲':'▼';
  $('s-chg').innerHTML = `<span class="${cls}">${arrow} ${Math.abs(chg).toFixed(2)}%</span>`;

  const metrics = [
    {l:'Market Cap',    v: fmtINR(d.market_cap)},
    {l:'P/E (TTM)',     v: fmtX(d.pe_ttm)},
    {l:'Forward P/E',  v: fmtX(d.pe_fwd)},
    {l:'P/B Ratio',    v: fmtX(d.pb)},
    {l:'EPS (TTM)',     v: d.eps ? '₹'+fmtN(d.eps) : '—'},
    {l:'52W High',      v: d['52w_high'] ? '₹'+fmtN(d['52w_high']) : '—'},
    {l:'52W Low',       v: d['52w_low']  ? '₹'+fmtN(d['52w_low'])  : '—'},
    {l:'50-DMA',        v: d.sma50  ? '₹'+fmtN(d.sma50)  : '—'},
    {l:'200-DMA',       v: d.sma200 ? '₹'+fmtN(d.sma200) : '—'},
    {l:'RSI (14)',      v: fmtN(d.rsi14,1)},
    {l:'Beta',          v: fmtN(d.beta)},
    {l:'ROE',           v: fmtPct(d.roe)},
    {l:'Profit Margin', v: fmtPct(d.profit_margin)},
    {l:'Div Yield',     v: d.dividend_yield ? fmtPct(d.dividend_yield) : '—'},
    {l:'Target (Mean)', v: d.target_mean ? '₹'+fmtN(d.target_mean) : '—'},
    {l:'Analyst View',  v: (d.recommendation||'—').toUpperCase()},
  ];

  $('metrics').innerHTML = metrics.map(m=>`
    <div class="metric"><div class="ml">${m.l}</div><div class="mv">${m.v}</div></div>
  `).join('');

  $('stock-header').style.display='block';
}

let es, rawMd='';

function startAnalysis(){
  const raw = $('ticker-input').value.trim().toUpperCase();
  if(!raw){ $('ticker-input').focus(); return; }

  $('error-box').style.display='none';
  $('stock-header').style.display='none';
  $('report-section').style.display='none';
  $('tabs').style.display='none';
  $('report-body').innerHTML='';
  $('analyze-btn').disabled=true;
  rawMd='';

  if(es) es.close();

  $('status-bar').style.display='flex';
  $('status-text').textContent='📡 Connecting to NSE/BSE data feed...';

  es = new EventSource(`/analyze?ticker=${encodeURIComponent(raw)}`);

  es.addEventListener('status', e => {
    $('status-text').textContent = JSON.parse('"'+e.data+'"');
  });

  es.addEventListener('stockdata', e => {
    renderStock(JSON.parse(e.data));
  });

  es.addEventListener('chunk', e => {
    rawMd += JSON.parse('"'+e.data+'"');

    if($('report-section').style.display!=='block'){
      $('report-section').style.display='block';
      $('tabs').style.display='flex';
      $('report-time').textContent = new Date().toLocaleString('en-IN',{timeZone:'Asia/Kolkata'});
    }

    const body = $('report-body');
    body.innerHTML = marked.parse(rawMd);
    const cur = document.createElement('span');
    cur.className='cursor'; body.appendChild(cur);
    cur.scrollIntoView({behavior:'smooth',block:'nearest'});
  });

  es.addEventListener('done', e => {
    $('status-bar').style.display='none';
    $('analyze-btn').disabled=false;
    es.close();
    $('report-body').innerHTML = marked.parse(rawMd);
  });

  es.addEventListener('error', e => {
    if(e.data){
      $('error-box').style.display='block';
      $('error-box').textContent = '⚠ ' + JSON.parse('"'+e.data+'"');
    }
    $('status-bar').style.display='none';
    $('analyze-btn').disabled=false;
    es.close();
  });

  es.onerror = () => {
    if(es.readyState===EventSource.CLOSED) return;
    $('status-bar').style.display='none';
    $('analyze-btn').disabled=false;
    es.close();
  };
}

function copyReport(){
  navigator.clipboard.writeText(rawMd).then(()=>{
    const b=$('.copy-btn');
    if(b){b.textContent='Copied!';setTimeout(()=>b.textContent='Copy Report',2000);}
  });
}
document.querySelector && (document.querySelector('.copy-btn') && null);
</script>
</body>
</html>

"""

@app.route("/")
def index():
    return HTML_PAGE, 200, {"Content-Type": "text/html"}

@app.route("/analyze")
def analyze():
    raw = request.args.get("ticker", "").strip()
    if not raw:
        return jsonify({"error": "No ticker provided"}), 400
    ticker = resolve_indian_ticker(raw)
    return Response(generate_analysis_stream(ticker), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
