# Sentiment Tracker

Real-time sentiment analysis for Swedish-listed stocks (OMX Stockholm, First North, Spotlight). Identifies trending tickers by aggregating mentions and sentiment across multiple sources.

**Status:** Live & running. Hourly automation via GitHub Actions. Local dashboard at localhost:8502 (Streamlit).

## Data Sources

| Source | Coverage | Speed |
|--------|----------|-------|
| **Reddit** | 6 subreddits (aktier, investingsweden, investing, stocks, StockMarket, wallstreetbets) | Real-time posts |
| **Flashback** | f487-aktier forum (robots.txt compliant) | Swedish investor discussions |
| **Twitter/X** | Keyword search for Swedish small caps | Instant social buzz |
| **allaaktier.se** | API: hot list sorted by 7-day trend | Validation & trend confirmation |

## How It Works

1. **Fetch** posts from all sources (Reddit `.json` endpoints + web scraping)
2. **Match** tickers using regex patterns against 713 Swedish tickers (CSV reference)
3. **Score** sentiment using keyword patterns (bullish/bearish/neutral) + upvote ratios
4. **Aggregate** mentions over 72-hour rolling window
5. **Calculate** trending score = mentions × avg_sentiment + velocity metrics
6. **Output** trending.json with top tickers, hot list, and detailed breakdowns

## Key Features

- **Velocity tracking**: mentions/day to catch acceleration before peaks
- **Small cap focus**: Twitter filters to First North & Spotlight stocks
- **Hourly updates**: Automated via GitHub Actions (`.github/workflows/collect.yml`)
- **Comprehensive coverage**: 713 tickers including OMX, First North, Spotlight
- **Sector breakdown**: Pie charts and sentiment analysis by sector
- **Hot list validation**: Cross-reference with allaaktier's 7-day trend data

## Tech Stack

- **Python 3.9**: Core runtime
- **Requests + BeautifulSoup**: Web scraping
- **Streamlit**: Dashboard UI
- **Pandas**: Data aggregation
- **GitHub Actions**: Hourly automation (commits updated `trending.json` to repo)

## Setup & Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Run sentiment analysis (generates trending.json)
python3 sentiment_service.py

# View dashboard
python3 -m streamlit run dashboard.py
# Navigate to http://localhost:8502
```

## Known Limitations

- **Reddit API denied**: Using public `.json` endpoints (no auth) instead
- **StockTwits**: API dead since 2021 — not integrated
- **Placera**: Vue.js SPA prevents HTML scraping — deferred
- **Twitter scraping**: Anti-bot measures may block searches — snscrape alternative pending

## Files

- `sentiment_service.py`: Main script (Reddit, Flashback, Twitter, allaaktier fetching)
- `dashboard.py`: Streamlit visualization
- `tickers_se.csv`: Reference database (ticker, company, sector, aliases)
- `trending.json`: Output with sentiment data & hot list
- `requirements.txt`: Python dependencies

## Next Steps

- [x] Migrate to GitHub Actions for reliable hourly automation
- [ ] Implement snscrape for better Twitter integration
- [ ] Add Placera scraping (if JavaScript rendering becomes viable)
- [ ] Expand to more Swedish forums (BlueCall, Avanza)
- [ ] Add sentiment persistence (tracking historical trends)
