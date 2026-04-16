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

app = Flask(__name__, static_folder="templates", static_url_path="")

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
    return app.send_static_file("index.html")

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
