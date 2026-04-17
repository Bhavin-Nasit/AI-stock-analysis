from flask import Flask, request, jsonify, render_template_string, Response
import subprocess
import sys
import os
import json
import yfinance as yf
import anthropic
from datetime import datetime, timedelta
import threading
import queue


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StockMind — AI Stock Analysis</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:ital,wght@0,400;0,500;1,400&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
<style>
:root {
  --bg: #070b10;
  --surface: #0d1520;
  --surface2: #111d2e;
  --border: #1e3048;
  --accent: #00d4ff;
  --accent2: #00ff9d;
  --warn: #ff6b35;
  --text: #e8f0fe;
  --muted: #5a7a9a;
  --card-glow: 0 0 40px rgba(0,212,255,0.06);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'DM Sans', sans-serif;
  min-height: 100vh;
  overflow-x: hidden;
}

/* ── Grid background ── */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(0,212,255,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,212,255,0.03) 1px, transparent 1px);
  background-size: 60px 60px;
  pointer-events: none;
  z-index: 0;
}

/* ── Ambient blobs ── */
.blob {
  position: fixed;
  border-radius: 50%;
  filter: blur(120px);
  pointer-events: none;
  z-index: 0;
}
.blob-1 { width: 600px; height: 600px; background: rgba(0,212,255,0.05); top: -200px; right: -100px; }
.blob-2 { width: 400px; height: 400px; background: rgba(0,255,157,0.04); bottom: -100px; left: -100px; }

/* ── Layout ── */
.wrapper { position: relative; z-index: 1; max-width: 960px; margin: 0 auto; padding: 0 24px 80px; }

/* ── Header ── */
header {
  padding: 48px 0 32px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.logo {
  font-family: 'Syne', sans-serif;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.25em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.logo-dot { width: 6px; height: 6px; background: var(--accent); border-radius: 50%; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.4;transform:scale(0.8)} }

h1 {
  font-family: 'Syne', sans-serif;
  font-size: clamp(2.2rem, 5vw, 3.6rem);
  font-weight: 800;
  line-height: 1.05;
  letter-spacing: -0.03em;
  background: linear-gradient(135deg, #ffffff 0%, #a0c4ff 50%, var(--accent) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 12px;
}

.tagline {
  color: var(--muted);
  font-size: 16px;
  font-weight: 300;
  letter-spacing: 0.01em;
}

/* ── Search ── */
.search-section { margin: 40px 0 0; width: 100%; max-width: 560px; }

.search-box {
  display: flex;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.search-box:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(0,212,255,0.12), 0 0 30px rgba(0,212,255,0.08);
}

.search-box input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  padding: 18px 20px;
  font-family: 'DM Mono', monospace;
  font-size: 18px;
  font-weight: 500;
  letter-spacing: 0.08em;
  color: var(--text);
  text-transform: uppercase;
}
.search-box input::placeholder { color: var(--muted); font-size: 14px; letter-spacing: 0.04em; text-transform: none; }

.search-btn {
  background: var(--accent);
  color: #000;
  border: none;
  padding: 18px 28px;
  font-family: 'Syne', sans-serif;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  cursor: pointer;
  transition: background 0.2s, transform 0.1s;
  display: flex;
  align-items: center;
  gap: 8px;
}
.search-btn:hover { background: #00eaff; }
.search-btn:active { transform: scale(0.98); }
.search-btn:disabled { background: var(--border); color: var(--muted); cursor: not-allowed; }

.popular-tickers {
  margin-top: 14px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
}
.ticker-chip {
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.1em;
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: 20px;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.2s;
}
.ticker-chip:hover { border-color: var(--accent); color: var(--accent); background: rgba(0,212,255,0.06); }

/* ── Status bar ── */
#status-bar {
  display: none;
  margin-top: 40px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 20px;
  font-family: 'DM Mono', monospace;
  font-size: 12px;
  color: var(--accent);
  letter-spacing: 0.05em;
  display: none;
  align-items: center;
  gap: 10px;
}
.status-spinner {
  width: 14px; height: 14px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Stock info cards ── */
#stock-header { display: none; margin-top: 40px; }

.stock-title-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 24px;
}
.stock-name {
  font-family: 'Syne', sans-serif;
  font-size: 1.8rem;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.stock-ticker-badge {
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  letter-spacing: 0.15em;
  background: rgba(0,212,255,0.1);
  border: 1px solid rgba(0,212,255,0.3);
  color: var(--accent);
  padding: 4px 10px;
  border-radius: 4px;
  vertical-align: middle;
  margin-left: 10px;
}
.stock-price-block { text-align: right; }
.stock-price {
  font-family: 'Syne', sans-serif;
  font-size: 2rem;
  font-weight: 700;
}
.stock-change { font-size: 14px; font-weight: 500; margin-top: 2px; }
.up { color: var(--accent2); }
.down { color: var(--warn); }

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 32px;
}

.metric-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  transition: border-color 0.2s;
}
.metric-card:hover { border-color: rgba(0,212,255,0.3); }
.metric-label {
  font-size: 10px;
  font-family: 'DM Mono', monospace;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 6px;
}
.metric-value {
  font-family: 'Syne', sans-serif;
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--text);
}

/* ── Report ── */
#report-section {
  display: none;
  margin-top: 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  box-shadow: var(--card-glow);
}

.report-header {
  padding: 18px 24px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--surface2);
}
.report-title {
  font-family: 'Syne', sans-serif;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--accent);
}
.report-meta {
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  color: var(--muted);
}

.report-body {
  padding: 28px 32px;
  line-height: 1.75;
  font-size: 15px;
  font-weight: 300;
}

/* Markdown styling */
.report-body h2 {
  font-family: 'Syne', sans-serif;
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
  margin: 28px 0 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.report-body h2:first-child { margin-top: 0; }

.report-body p { margin-bottom: 12px; color: #c8d8e8; }

.report-body ul, .report-body ol {
  padding-left: 20px;
  margin-bottom: 12px;
}
.report-body li { margin-bottom: 6px; color: #c8d8e8; }

.report-body strong { color: var(--text); font-weight: 600; }

.report-body em { color: var(--accent2); font-style: normal; }

/* Verdict box detection via JS */
.verdict-box {
  background: linear-gradient(135deg, rgba(0,212,255,0.06), rgba(0,255,157,0.04));
  border: 1px solid rgba(0,212,255,0.25);
  border-radius: 10px;
  padding: 20px 24px;
  margin-top: 8px;
}

/* Cursor blink */
.cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--accent);
  margin-left: 2px;
  animation: blink 1s step-end infinite;
  vertical-align: text-bottom;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }

/* ── Error ── */
#error-box {
  display: none;
  margin-top: 24px;
  padding: 16px 20px;
  background: rgba(255,107,53,0.08);
  border: 1px solid rgba(255,107,53,0.3);
  border-radius: 10px;
  color: var(--warn);
  font-size: 14px;
}

/* ── Responsive ── */
@media (max-width: 600px) {
  .metrics-grid { grid-template-columns: repeat(2, 1fr); }
  .report-body { padding: 20px 18px; }
  .stock-title-row { flex-direction: column; align-items: flex-start; }
  .stock-price-block { text-align: left; }
}
</style>
</head>
<body>

<div class="blob blob-1"></div>
<div class="blob blob-2"></div>

<div class="wrapper">
  <header>
    <div class="logo">
      <div class="logo-dot"></div>
      StockMind AI
    </div>
    <h1>Institutional-Grade<br>Stock Analysis</h1>
    <p class="tagline">Enter any ticker. Get a full AI investment report in seconds.</p>

    <div class="search-section">
      <div class="search-box">
        <input type="text" id="ticker-input" placeholder="Enter ticker symbol  e.g. AAPL, TSLA, NVDA" maxlength="10" autocomplete="off" autocorrect="off" spellcheck="false">
        <button class="search-btn" id="analyze-btn" onclick="startAnalysis()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
          Analyze
        </button>
      </div>
      <div class="popular-tickers">
        <span class="ticker-chip" onclick="setTicker('AAPL')">AAPL</span>
        <span class="ticker-chip" onclick="setTicker('TSLA')">TSLA</span>
        <span class="ticker-chip" onclick="setTicker('NVDA')">NVDA</span>
        <span class="ticker-chip" onclick="setTicker('MSFT')">MSFT</span>
        <span class="ticker-chip" onclick="setTicker('GOOGL')">GOOGL</span>
        <span class="ticker-chip" onclick="setTicker('AMZN')">AMZN</span>
        <span class="ticker-chip" onclick="setTicker('META')">META</span>
      </div>
    </div>
  </header>

  <!-- Status -->
  <div id="status-bar">
    <div class="status-spinner"></div>
    <span id="status-text">Initializing...</span>
  </div>

  <!-- Error -->
  <div id="error-box"></div>

  <!-- Stock Header Cards -->
  <div id="stock-header">
    <div class="stock-title-row">
      <div>
        <div class="stock-name" id="s-name">—<span class="stock-ticker-badge" id="s-ticker">—</span></div>
        <div style="font-size:13px;color:var(--muted);margin-top:4px;" id="s-sector">—</div>
      </div>
      <div class="stock-price-block">
        <div class="stock-price" id="s-price">—</div>
        <div class="stock-change" id="s-change">—</div>
      </div>
    </div>

    <div class="metrics-grid" id="metrics-grid"></div>
  </div>

  <!-- Report -->
  <div id="report-section">
    <div class="report-header">
      <span class="report-title">🤖 AI Investment Report</span>
      <span class="report-meta" id="report-time">—</span>
    </div>
    <div class="report-body" id="report-body"></div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

function setTicker(t) {
  $('ticker-input').value = t;
  $('ticker-input').focus();
}

$('ticker-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') startAnalysis();
});

function fmt(v, prefix='', suffix='', decimals=2) {
  if (v === null || v === undefined || v === 'N/A' || v === '') return '—';
  if (typeof v === 'number') {
    if (Math.abs(v) >= 1e12) return prefix + (v/1e12).toFixed(1) + 'T' + suffix;
    if (Math.abs(v) >= 1e9) return prefix + (v/1e9).toFixed(1) + 'B' + suffix;
    if (Math.abs(v) >= 1e6) return prefix + (v/1e6).toFixed(1) + 'M' + suffix;
    return prefix + v.toFixed(decimals) + suffix;
  }
  return prefix + v + suffix;
}

function showStatus(msg) {
  const bar = $('status-bar');
  bar.style.display = 'flex';
  $('status-text').textContent = msg;
}

function hideStatus() {
  $('status-bar').style.display = 'none';
}

function showError(msg) {
  const box = $('error-box');
  box.style.display = 'block';
  box.textContent = '⚠ ' + msg;
}

function hideError() {
  $('error-box').style.display = 'none';
}

function renderStockHeader(d) {
  $('s-name').innerHTML = d.name + `<span class="stock-ticker-badge">${d.ticker}</span>`;
  $('s-sector').textContent = [d.sector, d.industry].filter(x => x && x !== 'N/A').join(' · ');

  const price = typeof d.price === 'number' ? '$' + d.price.toFixed(2) : '—';
  $('s-price').textContent = price;

  const chg = d.change_pct;
  const arrow = chg >= 0 ? '▲' : '▼';
  const cls = chg >= 0 ? 'up' : 'down';
  $('s-change').innerHTML = `<span class="${cls}">${arrow} ${Math.abs(chg).toFixed(2)}%</span>`;

  const metrics = [
    { label: 'Market Cap',     value: fmt(d.market_cap, '$') },
    { label: 'P/E (TTM)',      value: fmt(d.pe_ratio, '', 'x', 1) },
    { label: 'Forward P/E',    value: fmt(d.forward_pe, '', 'x', 1) },
    { label: 'EPS',            value: fmt(d.eps, '$') },
    { label: '52W High',       value: fmt(d['52w_high'], '$') },
    { label: '52W Low',        value: fmt(d['52w_low'], '$') },
    { label: 'Beta',           value: fmt(d.beta, '', '', 2) },
    { label: 'Target Price',   value: fmt(d.target_price, '$') },
    { label: 'Analyst View',   value: (d.recommendation || '—').toUpperCase() },
    { label: 'Dividend Yield', value: d.dividend_yield && d.dividend_yield !== 'N/A' ? (d.dividend_yield*100).toFixed(2)+'%' : '—' },
  ];

  $('metrics-grid').innerHTML = metrics.map(m => `
    <div class="metric-card">
      <div class="metric-label">${m.label}</div>
      <div class="metric-value">${m.value}</div>
    </div>
  `).join('');

  $('stock-header').style.display = 'block';
}

let currentStream = null;
let rawMarkdown = '';
let cursorEl = null;

function startAnalysis() {
  const ticker = $('ticker-input').value.trim().toUpperCase();
  if (!ticker) { $('ticker-input').focus(); return; }

  // Reset UI
  hideError();
  $('stock-header').style.display = 'none';
  $('report-section').style.display = 'none';
  $('report-body').innerHTML = '';
  $('analyze-btn').disabled = true;
  rawMarkdown = '';

  if (currentStream) currentStream.close();

  showStatus('📡 Connecting to analysis pipeline...');

  const url = `/analyze?ticker=${encodeURIComponent(ticker)}`;
  const es = new EventSource(url);
  currentStream = es;

  es.addEventListener('status', e => {
    showStatus(JSON.parse('"' + e.data + '"'));
  });

  es.addEventListener('stockdata', e => {
    const d = JSON.parse(e.data);
    renderStockHeader(d);
  });

  es.addEventListener('chunk', e => {
    const text = JSON.parse('"' + e.data + '"');
    rawMarkdown += text;

    // Show report section
    if ($('report-section').style.display !== 'block') {
      $('report-section').style.display = 'block';
      $('report-time').textContent = new Date().toLocaleTimeString();
    }

    // Render markdown progressively
    const body = $('report-body');
    body.innerHTML = marked.parse(rawMarkdown);

    // Add cursor at end
    const cursor = document.createElement('span');
    cursor.className = 'cursor';
    body.appendChild(cursor);

    // Scroll into view smoothly
    body.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });

  es.addEventListener('done', e => {
    hideStatus();
    $('analyze-btn').disabled = false;
    currentStream.close();
    // Remove cursor
    const cursors = $('report-body').querySelectorAll('.cursor');
    cursors.forEach(c => c.remove());
    // Final render (clean)
    $('report-body').innerHTML = marked.parse(rawMarkdown);
  });

  es.addEventListener('error', e => {
    if (e.data) {
      showError(JSON.parse('"' + e.data + '"'));
    }
    hideStatus();
    $('analyze-btn').disabled = false;
    es.close();
  });

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) return;
    hideStatus();
    $('analyze-btn').disabled = false;
    es.close();
  };
}
</script>
</body>
</html>
"""


app = Flask(__name__)

# ── Anthropic client ──────────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

# ── Helper: fetch live stock data via yfinance ────────────────────────────────
def get_stock_data(ticker: str) -> dict:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="6mo")

        price = info.get("currentPrice") or info.get("regularMarketPrice", "N/A")
        prev_close = info.get("previousClose", price)
        change_pct = ((price - prev_close) / prev_close * 100) if isinstance(price, (int, float)) and isinstance(prev_close, (int, float)) else 0

        return {
            "ticker": ticker.upper(),
            "name": info.get("longName", ticker.upper()),
            "price": price,
            "change_pct": round(change_pct, 2),
            "market_cap": info.get("marketCap", "N/A"),
            "pe_ratio": info.get("trailingPE", "N/A"),
            "forward_pe": info.get("forwardPE", "N/A"),
            "eps": info.get("trailingEps", "N/A"),
            "revenue": info.get("totalRevenue", "N/A"),
            "profit_margin": info.get("profitMargins", "N/A"),
            "52w_high": info.get("fiftyTwoWeekHigh", "N/A"),
            "52w_low": info.get("fiftyTwoWeekLow", "N/A"),
            "avg_volume": info.get("averageVolume", "N/A"),
            "beta": info.get("beta", "N/A"),
            "dividend_yield": info.get("dividendYield", "N/A"),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "description": info.get("longBusinessSummary", "N/A"),
            "recommendation": info.get("recommendationKey", "N/A"),
            "target_price": info.get("targetMeanPrice", "N/A"),
            "recent_close": hist["Close"].iloc[-1] if not hist.empty else "N/A",
            "price_history": hist["Close"].tolist()[-30:] if not hist.empty else [],
        }
    except Exception as e:
        return {"error": str(e), "ticker": ticker.upper()}


# ── Helper: try to run your local agent scripts ───────────────────────────────
def run_agent_script(script_path: str, ticker: str) -> str:
    """Try to run a Python agent script and return its stdout, or empty string on failure."""
    if not os.path.exists(script_path):
        return ""
    try:
        result = subprocess.run(
            [sys.executable, script_path, ticker],
            capture_output=True, text=True, timeout=60
        )
        return result.stdout.strip()
    except Exception:
        return ""


# ── SSE streaming analysis ────────────────────────────────────────────────────
def generate_analysis_stream(ticker: str):
    """Generator that yields SSE events for the full analysis pipeline."""

    def sse(event: str, data: str):
        safe = data.replace("\n", "\\n")
        return f"event: {event}\ndata: {safe}\n\n"

    # 1. Fetch market data
    yield sse("status", "📡 Fetching live market data...")
    stock_data = get_stock_data(ticker)
    if "error" in stock_data:
        yield sse("error", f"Could not fetch data for {ticker}: {stock_data['error']}")
        return

    yield sse("stockdata", json.dumps(stock_data))
    yield sse("status", f"✅ Got data for {stock_data['name']}")

    # 2. Run local agent scripts if they exist (agents/ folder)
    agent_outputs = {}
    agent_scripts = {
        "technical": f"agents/technical_analyst.py",
        "fundamental": f"agents/fundamental_analyst.py",
        "sentiment": f"agents/sentiment_agent.py",
        "risk": f"agents/risk_agent.py",
    }
    for name, path in agent_scripts.items():
        out = run_agent_script(path, ticker)
        if out:
            agent_outputs[name] = out
            yield sse("status", f"✅ {name.title()} agent completed")

    # 3. Also try skill scripts
    skill_scripts = {
        "canslim": "skills/canslim_screener.py",
        "bubble": "skills/bubble_detector.py",
    }
    for name, path in skill_scripts.items():
        out = run_agent_script(path, ticker)
        if out:
            agent_outputs[name] = out
            yield sse("status", f"✅ {name.title()} skill completed")

    # 4. Build the master prompt for Claude
    yield sse("status", "🤖 Running AI analysis pipeline...")

    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.2f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    agent_section = ""
    if agent_outputs:
        agent_section = "\n\n## LOCAL AGENT OUTPUTS\n"
        for name, out in agent_outputs.items():
            agent_section += f"\n### {name.upper()} AGENT\n{out}\n"

    prompt = f"""You are an elite stock analyst. Produce a comprehensive investment research report for **{stock_data['name']} ({ticker.upper()})**.

## LIVE MARKET DATA
- Current Price: ${fmt(stock_data['price'])}
- Daily Change: {fmt(stock_data['change_pct'])}%
- Market Cap: ${fmt(stock_data['market_cap'])}
- P/E Ratio (TTM): {fmt(stock_data['pe_ratio'])}
- Forward P/E: {fmt(stock_data['forward_pe'])}
- EPS: {fmt(stock_data['eps'])}
- Revenue: ${fmt(stock_data['revenue'])}
- Profit Margin: {fmt(stock_data['profit_margin'])}
- 52-Week High: ${fmt(stock_data['52w_high'])}
- 52-Week Low: ${fmt(stock_data['52w_low'])}
- Beta: {fmt(stock_data['beta'])}
- Dividend Yield: {fmt(stock_data['dividend_yield'])}
- Sector: {stock_data['sector']}
- Industry: {stock_data['industry']}
- Analyst Target Price: ${fmt(stock_data['target_price'])}
- Analyst Recommendation: {stock_data['recommendation']}

## COMPANY OVERVIEW
{stock_data['description'][:600]}...
{agent_section}

---

Generate a structured investment report with the following sections. Use markdown formatting with headers (##), bullet points, and **bold** for key figures.

## 1. EXECUTIVE SUMMARY
One paragraph investment thesis and verdict.

## 2. FUNDAMENTAL ANALYSIS
- Valuation assessment (P/E vs sector, growth-adjusted)
- Revenue and earnings quality
- Balance sheet strength
- Profitability metrics

## 3. TECHNICAL ANALYSIS
- Current trend (based on 52w range positioning)
- Key support and resistance levels
- Momentum assessment
- Volume and volatility context (Beta: {fmt(stock_data['beta'])})

## 4. BULL CASE 🟢
3-4 specific reasons the stock could outperform.

## 5. BEAR CASE 🔴
3-4 specific risks and downside scenarios.

## 6. RISK ASSESSMENT
Rate each: Market Risk, Fundamental Risk, Valuation Risk (Low/Medium/High) with brief justification.

## 7. INVESTMENT VERDICT
- **Recommendation**: BUY / HOLD / SELL / WATCH
- **Target Price Range**: $X - $Y (12-month)
- **Conviction Level**: Low / Medium / High
- **Time Horizon**: Short / Medium / Long term
- **Suitable For**: (investor profile)

Be specific, data-driven, and actionable. Use the actual numbers provided."""

    # 5. Stream Claude response
    yield sse("status", "📝 Generating investment report...")
    try:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            for text in stream.text_stream:
                yield sse("chunk", text)
    except Exception as e:
        yield sse("error", f"AI analysis failed: {str(e)}")
        return

    yield sse("done", "Report complete")


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return HTML_PAGE, 200, {"Content-Type": "text/html"}

@app.route("/analyze")
def analyze():
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "No ticker provided"}), 400
    return Response(generate_analysis_stream(ticker), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
