# Indian Stock Score Dashboard

A Render-ready Flask dashboard that generates an 8-section Indian stock analysis report from an NSE/BSE stock name or ticker.

The scoring model mirrors the uploaded trading-skills rubric:

| Dimension | Weight |
| --- | ---: |
| Technical strength | 25% |
| Fundamental quality | 25% |
| Sentiment and momentum | 20% |
| Risk profile | 15% |
| Thesis conviction | 15% |

The app fetches public market data with `yfinance`, scores each dimension out of 100, creates a composite score, and renders an HTML report with a downloadable PDF.

## Run Locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8050`, then enter examples such as:

- `Reliance`
- `TCS`
- `INFY.NS`
- `HDFCBANK`
- `TATAMOTORS.NS`

## Render Deployment

The repository includes both `render.yaml` and `Procfile`.
It also pins Python 3.11 through `.python-version` and `PYTHON_VERSION`
because the data-science dependencies are safer on Python 3.11 than on
Render's newest default runtime.

For Render:

1. Connect this GitHub repository to Render.
2. Create a new Web Service.
3. Use Python environment.
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app`

If Render detects `render.yaml`, it can provision the service automatically.

## Data Notes

- NSE tickers usually end with `.NS`.
- BSE tickers usually end with `.BO`.
- Common Indian stock names are mapped automatically, but exact tickers are best.
- Free public feeds can have gaps in Indian analyst, insider, short-interest, and social sentiment data. Missing data is scored neutrally and called out in the report.

## Disclaimer

This dashboard is for educational and research purposes only. It is not financial advice, investment advice, or a recommendation to buy, sell, or hold any security. Verify all data independently and consult a licensed financial advisor before making investment decisions.
