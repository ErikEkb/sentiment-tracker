#!/usr/bin/env python3
"""
Sentiment Service — v1

Fetches recent posts from Swedish-investing subreddits via Reddit's
public .json endpoints, identifies mentions of OMX Stockholm tickers
using the reference CSV, scores sentiment using a simple Swedish
keyword + upvote heuristic, and writes the top 5 trending tickers
(by positive sentiment momentum) to trending.json.

Usage:
    python sentiment_service.py

V2 plan: replace `score_post_sentiment` with a Swedish sentiment
model (KB-BERT or multilingual transformer). Everything else stays
the same.
"""

import csv
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- Configuration ---
SUBREDDITS = [
    "aktier",           # Swedish stocks
    "investingsweden",  # Swedish investing discussion (often low-volume)
    "investing",        # General investing discussion
    "stocks",           # Stock market discussion
    "StockMarket",      # Stock market news and analysis
    "wallstreetbets",   # High-risk trading community
]
PLACERA_BASE_URL = "https://forum.placera.se/bolag/"
PLACERA_USER_AGENT = "sentiment-tracker by /u/No-Negotiation1177"
# --- Flashback config ---
FLASHBACK_FORUM_URL = "https://www.flashback.org/f487-aktier-60504"
FLASHBACK_THREAD_URL = "https://www.flashback.org/t{thread_id}"
FLASHBACK_THREAD_PAGE_URL = "https://www.flashback.org/t{thread_id}p{page}"
FLASHBACK_MAX_THREADS = 10           # most-active threads to scan per run
FLASHBACK_CRAWL_DELAY_SEC = 5        # robots.txt asks for 5s between requests
FLASHBACK_USER_AGENT = "sentiment-tracker by /u/No-Negotiation1177"
ALLAAKTIER_API_URL = "https://allaaktier.se/api/companies/list"
ALLAAKTIER_WEB_URL = "https://allaaktier.se/"
ALLAAKTIER_MAX_HOT = 25
PLACERA_COMPANIES = [
    "minesto", "sinch", "truecaller", "storytel", "embracer", "stillfront",
    "betsson", "kindred", "evolution", "thq-nordic", "mtg", "kambi",
    "catena", "sbb", "kastellet", "clavister", "sensys-traffic",
    "a1-moller", "danske-bank", "nordea-bank", "handelsbanken", "seb",
    "swedbank", "skandia", "länsförsäkringar", "folksam", "trygg-hansa"
]
TICKERS_CSV = "tickers_se.csv"
OUTPUT_JSON = "trending.json"
USER_AGENT = "sentiment-tracker by /u/No-Negotiation1177"
POSTS_PER_SUB = 50              # Reddit max per subreddit (reduced to avoid rate limits)
WINDOW_HOURS = 72                # rolling window (3 days)
TOP_N = 5                        # how many trending tickers to surface
MIN_MENTIONS = 2                 # minimum mentions in window to qualify

# --- Swedish sentiment lexicon (v1 — REPLACE FOR V2) ---
POSITIVE_WORDS = {
    # Swedish — buying / bullish / strength
    "köp", "köper", "köpläge", "köpvärd", "köpvärt", "köpa",
    "bra", "bull", "bullish", "bullar",
    "stark", "starkt", "starkare", "starkast",
    "tillväxt", "tillväxer", "vinst", "vinster",
    "positiv", "positivt", "positiva",
    "upp", "uppgång", "uppåt", "stiger", "stigit", "steg",
    "lyft", "lyfter", "höjt", "höjs", "höjning",
    "intressant", "spännande", "lovande",
    "rekommendera", "rekommenderar", "rekommendation",
    "rapport", "stark rapport", "bra rapport",
    "guld", "lyckad", "lyckas",
    # English (often used in Swedish posts)
    "buy", "long", "rally", "moon", "yolo",
}

NEGATIVE_WORDS = {
    # Swedish — selling / bearish / weakness
    "sälj", "säljer", "säljläge", "sälja",
    "dålig", "dåligt", "dåliga",
    "svag", "svagt", "svagare", "svagast",
    "förlust", "förluster", "förlorar",
    "negativ", "negativt", "negativa",
    "ned", "nedåt", "nedgång", "faller", "fall", "fallit", "föll",
    "kris", "krasch", "kraschar", "kraschat",
    "skit", "skitig", "skräp", "trash",
    "blanka", "blankning", "shorta", "shortar",
    "hopplös", "hopplöst",
    "konkurs", "rasar", "rasat",
    # English
    "sell", "short", "bearish", "bear", "dump", "crash",
}

# --- Stoplist: single-word aliases / bare tickers that collide with common words.
#     Aliases matching this list are DROPPED at load time.
#     Bare tickers matching this list are matched CASE-SENSITIVELY (so "BUY"
#     matches the literal ticker BUY but not the English word "buy"). ---
COMMON_WORDS_LOWER = {
    # English — most common 1–5 char words that overlap real tickers
    "i", "a", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is",
    "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we", "ok",
    "all", "and", "any", "are", "but", "buy", "can", "did", "for", "get", "had",
    "has", "her", "him", "his", "how", "let", "may", "new", "now", "not", "old",
    "one", "our", "out", "owe", "see", "say", "she", "ten", "the", "too", "top",
    "two", "use", "via", "war", "was", "way", "who", "why", "win", "yes", "yet",
    "you",
    "also", "back", "bear", "been", "bell", "best", "bond", "both", "bull",
    "buys", "case", "cash", "come", "core", "cost", "data", "deal", "does",
    "done", "down", "drop", "easy", "edge", "etfs", "even", "ever", "fact",
    "fail", "fall", "fast", "fees", "fell", "find", "fine", "fire", "five",
    "flow", "form", "free", "from", "fund", "game", "gain", "gave", "gets",
    "give", "gone", "good", "grew", "hard", "have", "head", "hear", "held",
    "help", "here", "high", "hold", "home", "hope", "huge", "idea", "into",
    "join", "just", "keep", "kind", "knew", "know", "last", "lead", "left",
    "less", "life", "like", "line", "live", "long", "look", "lose", "loss",
    "lots", "love", "made", "main", "make", "many", "more", "most", "move",
    "much", "must", "name", "need", "news", "next", "nice", "none", "note",
    "once", "only", "open", "over", "owns", "pair", "part", "past", "path",
    "pays", "peak", "pick", "plan", "play", "plus", "post", "pump", "puts",
    "raid", "rate", "rise", "real", "risk", "rose", "safe", "said", "same",
    "save", "saves", "sees", "seen", "self", "sell", "send", "ship", "shop",
    "show", "side", "size", "slow", "snap", "sold", "some", "stay", "step",
    "stop", "such", "sure", "swap", "take", "talk", "tank", "tell", "term",
    "than", "that", "them", "then", "they", "this", "thus", "time", "told",
    "took", "trip", "true", "turn", "type", "unit", "used", "uses", "very",
    "view", "wait", "want", "ways", "weak", "well", "went", "were", "what",
    "when", "wide", "wife", "will", "wins", "with", "wont", "work", "year",
    "your", "yolo", "moon", "rich", "rate", "yield",
    "after", "again", "alone", "along", "among", "asset", "avoid", "begin",
    "below", "blame", "block", "board", "brain", "break", "bring", "brand",
    "brave", "bring", "build", "built", "buyer", "bytes", "calls", "cared",
    "cares", "carry", "cause", "cents", "chain", "check", "claim", "clean",
    "clear", "click", "close", "could", "count", "cover", "cycle", "daily",
    "dates", "deals", "doing", "doubt", "drawn", "drive", "dying", "early",
    "earns", "enjoy", "enter", "entry", "equal", "every", "exits", "extra",
    "facts", "field", "fight", "final", "first", "fixed", "flags", "folks",
    "force", "found", "frame", "fresh", "front", "fully", "funds", "gains",
    "gives", "given", "going", "great", "group", "grows", "gross", "guess",
    "happy", "holds", "homes", "hopes", "hours", "house", "human", "ideas",
    "inner", "issue", "items", "jumps", "knows", "large", "later", "leads",
    "least", "level", "lives", "loans", "local", "looks", "loses", "loved",
    "lower", "lucky", "makes", "media", "might", "miner", "moved", "money",
    "month", "mouse", "moves", "names", "needs", "never", "newer", "noise",
    "north", "noted", "notes", "ought", "owned", "owner", "pairs", "paper",
    "parts", "party", "peace", "peaks", "phase", "phone", "piece", "place",
    "plain", "plans", "plays", "point", "power", "prime", "print", "prior",
    "probe", "proxy", "quick", "quiet", "quite", "rally", "raise", "rates",
    "ready", "right", "risen", "rises", "risks", "round", "sales", "scene",
    "scope", "screw", "seems", "sells", "sense", "share", "short", "shot",
    "shows", "since", "sized", "skill", "small", "solid", "solve", "south",
    "spend", "spent", "stake", "start", "state", "stays", "still", "stock",
    "stops", "store", "story", "study", "stuff", "style", "swing", "sword",
    "table", "takes", "talks", "taxed", "taxes", "teams", "tells", "their",
    "there", "these", "thick", "thing", "think", "third", "those", "three",
    "times", "today", "total", "touch", "tough", "tower", "track", "trade",
    "train", "trend", "tries", "trust", "truth", "tweet", "under", "until",
    "upper", "usual", "value", "video", "views", "wages", "wants", "watch",
    "water", "weeks", "weird", "where", "which", "while", "white", "whole",
    "whose", "wider", "winds", "world", "worse", "worth", "would", "write",
    "wrong", "yards", "years", "young",
    # Swedish 1–5 char words that overlap tickers
    "av", "är", "den", "det", "du", "en", "ett", "att", "som", "vi", "han",
    "hon", "min", "din", "sin", "här", "där", "ja", "nej", "men", "men", "om",
    "på", "och", "då", "nu", "se", "ge", "gå", "ha", "ta", "vad", "kan", "för",
    "när", "har", "var", "vid", "kr",
    "alla", "andra", "ingen", "några", "kunde", "skall", "skulle", "kommer",
    "redan", "första", "sista", "alltid", "aldrig", "sverige", "svensk", "mer",
    "mest", "minst", "bra", "kanske", "säkert", "mycket", "lite", "ofta",
    "köpa", "sälja", "köper", "säljer", "uppe", "nere", "tror", "tycker",
    "värde", "aktie", "aktien", "bolag", "bolaget", "marknad", "marknaden",
    "rapport", "rapporten", "vinst", "förlust", "tillväxt", "kursen",
    # Country / region / generic finance words that auto-generated aliases
    # often produce as single-word fragments
    "sweden", "sverige", "norge", "finland", "denmark", "oslo",
    "stockholm", "copenhagen", "helsinki", "nordic", "europe", "european",
    "world", "global", "asia", "asian", "north", "south", "east", "west",
    "model", "modell", "models", "modeller", "shares", "share",
    "group", "gruppen", "bank", "banks", "banking", "fond", "fonder",
    "company", "companies", "holding", "holdings", "capital", "kapital",
    "mining", "energy", "energi", "tech", "teknik",
}

# --- Ticker reference loader ---
def _is_dangerous_alias(alias: str) -> bool:
    """An alias is risky if it's a SINGLE WORD that overlaps a common English/Swedish word.
    Multi-word aliases (containing space, hyphen, etc.) are always kept — they're distinctive."""
    if not alias:
        return True
    # Multi-word aliases / aliases with special characters are safe (distinctive)
    if any(c in alias for c in " -&.()/'\""):
        return False
    return alias.lower() in COMMON_WORDS_LOWER


def load_tickers(csv_path: Path):
    """Return list of ticker dicts. Each: {ticker, company_name, aliases, segment, sector}.
    Aliases are pre-filtered to drop single-word common-word aliases (e.g. 'i', 'my',
    'sweden', 'risk') that would cause massive false positives."""
    tickers = []
    dropped_aliases = 0
    for_logging = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = [a.strip().lower() for a in row["aliases"].split("|") if a.strip()]
            kept = [a for a in raw if not _is_dangerous_alias(a)]
            dropped_aliases += len(raw) - len(kept)
            row["aliases"] = kept
            tickers.append(row)
    if dropped_aliases:
        print(f"  (filtered {dropped_aliases} common-word aliases to prevent false positives)")
    return tickers


def build_matchers(tickers):
    """Build one compiled regex per ticker. Bare ticker symbols that match common
    English/Swedish words are matched CASE-SENSITIVELY (so 'BUY' matches the literal
    ticker but 'buy' the verb does not). Cashtags and multi-word aliases stay
    case-insensitive via inline (?i:...) groups."""
    matchers = []
    for t in tickers:
        ticker = t["ticker"]                     # canonical, e.g. "VOLV-B" or "BUY"
        ticker_lower = ticker.lower()
        ci_variants = set()                       # case-INSENSITIVE alternatives
        cs_variants = set()                       # case-SENSITIVE alternatives

        # Cashtag is always unambiguous → case-insensitive
        ci_variants.add("$" + ticker_lower)

        # Bare ticker: case-sensitive if it's a common word, else case-insensitive
        if ticker_lower in COMMON_WORDS_LOWER:
            cs_variants.add(ticker)               # require literal uppercase form
        else:
            ci_variants.add(ticker_lower)

        # Aliases were already filtered in load_tickers; remaining ones are safe
        for alias in t["aliases"]:
            ci_variants.add(alias)

        parts = []
        if ci_variants:
            ci_alts = "|".join(re.escape(v) for v in sorted(ci_variants, key=len, reverse=True))
            parts.append(f"(?i:{ci_alts})")
        if cs_variants:
            cs_alts = "|".join(re.escape(v) for v in sorted(cs_variants, key=len, reverse=True))
            parts.append(cs_alts)
        if not parts:
            continue

        pattern = r"(?<![\w$])(?:" + "|".join(parts) + r")(?!\w)"
        # NOTE: no global IGNORECASE — case-sensitivity is per-variant via (?i:...)
        matchers.append((re.compile(pattern), t))
    return matchers


def find_mentions(text: str, matchers):
    """Return set of ticker symbols mentioned in text. Uses ORIGINAL case so the
    case-sensitive matchers can distinguish 'BUY' (ticker) from 'buy' (verb)."""
    return {t["ticker"] for regex, t in matchers if regex.search(text)}


# --- Sentiment scoring (v1 — REPLACE THIS FUNCTION FOR V2) ---
def score_post_sentiment(post: dict):
    """Score a post's sentiment in [-1, 1] using Swedish keyword counts.
    Returns (score, label).

    V1: simple lexicon counter. Naive but cheap and language-aware.
    V2: replace with KB-BERT / multilingual transformer. Same signature.
    """
    text = (post.get("title", "") + " " + post.get("selftext", "")).lower()
    words = re.findall(r"\w+", text, re.UNICODE)
    pos = sum(1 for w in words if w in POSITIVE_WORDS)
    neg = sum(1 for w in words if w in NEGATIVE_WORDS)
    if pos + neg == 0:
        return 0.0, "neutral"
    score = (pos - neg) / (pos + neg)
    label = "bullish" if score > 0.2 else "bearish" if score < -0.2 else "neutral"
    return score, label


# --- Reddit JSON fetcher ---
def fetch_subreddit(name: str, limit: int = 100):
    """Fetch recent posts from a subreddit via Reddit's public .json endpoint.
    Returns a list of normalized post dicts. Returns [] on failure (logged)."""
    # Try /new first, fall back to /top if empty
    urls = [
        f"https://www.reddit.com/r/{name}/new.json?limit={limit}",
        f"https://www.reddit.com/r/{name}/top.json?t=week&limit={limit}",
    ]
    
    children = []
    for url_attempt in urls:
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.get(url_attempt, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            children = data.get("children", [])
            if children:
                break  # Got posts, stop trying
        except requests.RequestException as e:
            if url_attempt == urls[-1]:  # Only print on last failure
                print(f"  ! r/{name} fetch failed: {e}", file=sys.stderr)
            continue
    
    if len(children) == 0:
        print(f"  ! r/{name}: No posts found via /new or /top (week)", file=sys.stderr)
    
    posts = []
    for c in children:
        d = c.get("data", {})
        posts.append({
            "id": d.get("id"),
            "subreddit": d.get("subreddit"),
            "title": d.get("title", "") or "",
            "selftext": d.get("selftext", "") or "",
            "score": d.get("score", 0),
            "upvote_ratio": d.get("upvote_ratio", 0.5),
            "num_comments": d.get("num_comments", 0),
            "created_utc": d.get("created_utc", 0),
            "permalink": "https://www.reddit.com" + d.get("permalink", ""),
        })
    return posts


def fetch_placera_company(company_slug: str, limit: int = 20):
    """Fetch recent posts from a Placera company forum using Selenium.
    Returns a list of normalized post dicts. Returns [] on failure."""
    url = f"{PLACERA_BASE_URL}{company_slug}"
    posts = []
    
    # Set up Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(f"--user-agent={PLACERA_USER_AGENT}")
    
    driver = None
    try:
        # Initialize the driver
        from selenium.webdriver.chrome.service import Service
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Navigate to the page
        driver.get(url)
        
        # Wait for the page to load (look for some content indicator)
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        
        # Additional wait for dynamic content
        time.sleep(3)
        
        # Try to find posts using different selectors
        post_selectors = [
            "div[data-testid*='post']",
            "article",
            ".post",
            "[class*='post']",
            ".thread-item",
            ".message",
            ".discussion-item"
        ]
        
        post_elements = []
        for selector in post_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    post_elements = elements[:limit]
                    break
            except:
                continue
        
        if not post_elements:
            print(f"  ! Placera {company_slug}: No posts found with any selector", file=sys.stderr)
            return []
        
        for i, element in enumerate(post_elements[:limit]):
            try:
                # Extract post data
                title = ""
                content = ""
                score = 0
                num_comments = 0
                created_utc = time.time() - (i * 3600)  # Rough estimate
                
                # Try to find title
                title_selectors = ["h3", ".title", "[class*='title']", "a[href*='thread']"]
                for t_sel in title_selectors:
                    try:
                        title_elem = element.find_element(By.CSS_SELECTOR, t_sel)
                        title = title_elem.text.strip()
                        if title:
                            break
                    except:
                        continue
                
                # Try to find content
                content_selectors = [".content", ".body", ".text", "[class*='content']", "p"]
                for c_sel in content_selectors:
                    try:
                        content_elem = element.find_element(By.CSS_SELECTOR, c_sel)
                        content = content_elem.text.strip()
                        if content:
                            break
                    except:
                        continue
                
                # Try to find score/likes
                score_selectors = [".score", ".likes", ".upvotes", "[class*='score']"]
                for s_sel in score_selectors:
                    try:
                        score_elem = element.find_element(By.CSS_SELECTOR, s_sel)
                        score_text = score_elem.text.strip()
                        score = int(''.join(filter(str.isdigit, score_text))) if score_text else 0
                        if score > 0:
                            break
                    except:
                        continue
                
                # Try to find comment count
                comment_selectors = [".comments", ".replies", "[class*='comment']"]
                for com_sel in comment_selectors:
                    try:
                        comment_elem = element.find_element(By.CSS_SELECTOR, com_sel)
                        comment_text = comment_elem.text.strip()
                        num_comments = int(''.join(filter(str.isdigit, comment_text))) if comment_text else 0
                        if num_comments > 0:
                            break
                    except:
                        continue
                
                # Create post entry
                post_id = f"placera-{company_slug}-{i}"
                permalink = f"{url}#post-{i}"
                
                posts.append({
                    "id": post_id,
                    "subreddit": f"placera-{company_slug}",
                    "title": title,
                    "selftext": content,
                    "score": score,
                    "upvote_ratio": 0.5,  # Placera doesn't show upvote ratio
                    "num_comments": num_comments,
                    "created_utc": created_utc,
                    "permalink": permalink,
                })
                
            except Exception as e:
                print(f"  ! Error parsing Placera post {i}: {e}", file=sys.stderr)
                continue
        
    except Exception as e:
        print(f"  ! Placera {company_slug} Selenium error: {e}", file=sys.stderr)
        return []
    finally:
        if driver:
            driver.quit()
    
    return posts


def fetch_allaaktier_hot(max_items=25):
    """Fetch recent hot companies from allaaktier.se via their JSON API."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": ALLAAKTIER_WEB_URL,
    }
    try:
        resp = requests.get(ALLAAKTIER_API_URL, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  ! allaaktier API fetch failed: {e}", file=sys.stderr)
        return []
    except ValueError as e:
        print(f"  ! allaaktier JSON decode failed: {e}", file=sys.stderr)
        return []

    posts = []
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        rows = data["data"]
    elif isinstance(data, list):
        rows = data
    else:
        print("  ! allaaktier API returned unexpected shape", file=sys.stderr)
        return []

    for row in rows[:max_items]:
        ticker = row.get("ticker") or row.get("symbol") or row.get("slug")
        name = row.get("name") or row.get("companyName") or ""
        trend7d = row.get("trend7d")
        discord = row.get("discordMembers")
        facebook = row.get("facebookMembers")
        market_cap = row.get("marketCap")
        nordnet = row.get("nordnetOwners") or row.get("avanzaOwners") or row.get("owners")
        slug = row.get("slug")
        if not ticker or not name:
            continue
        hotspot = {
            "ticker": ticker.upper(),
            "company_name": name,
            "trend7d": trend7d,
            "discord_members": discord,
            "facebook_members": facebook,
            "market_cap": market_cap,
            "owner_count": nordnet,
            "detail_url": f"https://allaaktier.se/bolag/{slug}" if slug else ALLAAKTIER_WEB_URL,
        }
        posts.append(hotspot)
    return posts


# --- Flashback forum scraping ---
def parse_flashback_date(date_str: str, now: datetime = None):
    """Convert a Flashback date string to a Unix timestamp.
    Handles three formats: '2025-10-25, 07:14', 'Idag 21:50', 'Igår 14:30'.
    Returns 0 if parsing fails (post will be filtered out by window cutoff)."""
    if now is None:
        now = datetime.now()
    s = (date_str or "").strip()
    try:
        if s.startswith("Idag"):
            t = s.replace("Idag", "").strip()
            h, m = (int(x) for x in t.split(":"))
            dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        elif s.startswith("Igår") or s.startswith("Ig\xe5r"):
            t = re.sub(r"^(Igår|Ig\xe5r)", "", s).strip()
            h, m = (int(x) for x in t.split(":"))
            dt = (now - timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
        else:
            # "2025-10-25, 07:14"
            dt = datetime.strptime(s, "%Y-%m-%d, %H:%M")
        return dt.timestamp()
    except (ValueError, AttributeError):
        return 0.0


def parse_title_for_ticker(title: str, tickers_lookup: dict):
    """Extract a ticker and company name from a Flashback thread title.
    Format observed: 'Company Name - TICKER [- TICKER2 ...] [(Country)]'.
    Returns (ticker, company_name, in_reference_bool) or (None, None, False).
    If no candidate ticker matches the reference CSV, the FIRST candidate from
    the title is returned as a 'discovered' ticker so we still capture trending
    on names not yet in the CSV (Silex, GME, etc.)."""
    if not title:
        return None, None, False
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    parts = [p.strip() for p in cleaned.split(" - ")]
    if len(parts) < 2:
        return None, None, False
    company = parts[0]
    # Try each candidate against the reference (preferred — gives us metadata)
    for candidate in parts[1:]:
        normalized = re.sub(r"\s+", "-", candidate).upper()
        if normalized in tickers_lookup:
            ref = tickers_lookup[normalized]
            return normalized, ref.get("company_name", company), True
    # No reference match — return first candidate as a discovered ticker
    discovered = re.sub(r"\s+", "-", parts[1]).upper()
    return discovered, company, False


def fetch_flashback_index():
    """Fetch the f487 (Aktier) index page and return list of thread metadata.
    Each item: {thread_id, title, last_page}. Stickies excluded. Sorted by
    forum order (most recently active first)."""
    headers = {"User-Agent": FLASHBACK_USER_AGENT, "Accept-Language": "sv,en;q=0.5"}
    try:
        resp = requests.get(FLASHBACK_FORUM_URL, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! Flashback index fetch failed: {e}", file=sys.stderr)
        return []
    resp.encoding = "iso-8859-1"  # Flashback explicitly declares ISO-8859-1
    soup = BeautifulSoup(resp.text, "html.parser")
    threads = []
    for link in soup.find_all("a", id=re.compile(r"^thread_title_\d+$")):
        # Skip stickies — their parent <tr> has class tr_sticky
        tr = link.find_parent("tr")
        if tr and "tr_sticky" in (tr.get("class") or []):
            continue
        thread_id = link["id"].replace("thread_title_", "")
        title = link.get_text(strip=True)
        # Find last-page indicator if the thread is multi-page
        last_page = 1
        if tr:
            last_page_link = tr.find("a", class_="thread-pagenav-lastpage")
            if last_page_link:
                m = re.search(r"\d+", last_page_link.get_text())
                if m:
                    last_page = int(m.group())
        threads.append({
            "thread_id": thread_id,
            "title": title,
            "last_page": last_page,
        })
    return threads


def fetch_flashback_thread_posts(thread_id: str, last_page: int, ticker_for_thread: str = None):
    """Fetch the LAST page of a Flashback thread and return post dicts.
    Uses the existing post schema so they merge cleanly with Reddit posts.
    If ticker_for_thread is set, each post is tagged with explicit_ticker."""
    if last_page > 1:
        url = FLASHBACK_THREAD_PAGE_URL.format(thread_id=thread_id, page=last_page)
    else:
        url = FLASHBACK_THREAD_URL.format(thread_id=thread_id)
    headers = {"User-Agent": FLASHBACK_USER_AGENT, "Accept-Language": "sv,en;q=0.5"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! Flashback t{thread_id} fetch failed: {e}", file=sys.stderr)
        return []
    resp.encoding = "iso-8859-1"
    soup = BeautifulSoup(resp.text, "html.parser")
    now = datetime.now()
    posts = []
    for post_div in soup.find_all("div", class_="post", attrs={"data-postid": True}):
        post_id = post_div.get("data-postid", "")
        # Date: text content of post-heading, regex out the timestamp
        heading = post_div.find("div", class_="post-heading")
        heading_text = heading.get_text(separator=" ", strip=True) if heading else ""
        date_match = re.search(
            r"(\d{4}-\d{2}-\d{2}, \d{1,2}:\d{2}|Idag \d{1,2}:\d{2}|Ig[åa]r \d{1,2}:\d{2})",
            heading_text,
        )
        post_date_str = date_match.group(1) if date_match else ""
        created_utc = parse_flashback_date(post_date_str, now=now)
        # Body: text content of div.post_message
        msg_div = post_div.find("div", class_="post_message")
        body_text = msg_div.get_text(separator=" ", strip=True) if msg_div else ""
        if not body_text:
            continue
        post = {
            "id": f"flashback-{post_id}",
            "subreddit": f"flashback-t{thread_id}",  # reuse field for source label
            "title": "",                              # no per-post title on Flashback
            "selftext": body_text,
            "score": 0,                               # Flashback has no upvotes
            "upvote_ratio": 0.5,                      # neutral default
            "num_comments": 0,
            "created_utc": created_utc,
            "permalink": f"https://www.flashback.org/sp{post_id}",  # deep-link to post
        }
        if ticker_for_thread:
            post["explicit_ticker"] = ticker_for_thread
        posts.append(post)
    return posts


def fetch_flashback_all(tickers_lookup):
    """Top-level Flashback fetcher: index + most-active threads' last pages.
    Returns combined list of post dicts ready for the aggregator."""
    print("Fetching Flashback (f487-aktier)...")
    threads = fetch_flashback_index()
    if not threads:
        print("  ! Flashback index returned no threads")
        return []
    threads = threads[:FLASHBACK_MAX_THREADS]
    print(f"  Index: {len(threads)} active threads to scan")
    all_posts = []
    for i, t in enumerate(threads, 1):
        time.sleep(FLASHBACK_CRAWL_DELAY_SEC)  # respect robots.txt
        ticker, company, in_ref = parse_title_for_ticker(t["title"], tickers_lookup)
        if ticker:
            marker = ticker if in_ref else f"{ticker} (discovered)"
        else:
            marker = "(unparseable)"
        posts = fetch_flashback_thread_posts(t["thread_id"], t["last_page"], ticker)
        # Tag discovered tickers (not in reference CSV) with their company_name
        # so the aggregator can show useful info for them.
        if ticker and not in_ref:
            for p in posts:
                p["discovered_company_name"] = company
        print(f"  {i}/{len(threads)} t{t['thread_id']} '{t['title'][:40]}' → {marker}: {len(posts)} posts")
        all_posts.extend(posts)
    return all_posts


# --- Aggregation ---
def aggregate_trending(posts, matchers, tickers, window_hours, min_mentions, top_n):
    """Aggregate ticker mentions and sentiment across recent posts.
    Returns top_n trending tickers by (mentions × avg_sentiment), positive only."""
    cutoff = time.time() - window_hours * 3600
    by_ticker = {}

    for post in posts:
        if post["created_utc"] < cutoff:
            continue
        text = post["title"] + " " + post["selftext"]
        # If the source pre-attributed a ticker (e.g. Flashback thread title),
        # use it directly; otherwise scan body text for matches.
        if post.get("explicit_ticker"):
            mentioned = {post["explicit_ticker"]}
        else:
            mentioned = find_mentions(text, matchers)
        if not mentioned:
            continue
        sent_score, _ = score_post_sentiment(post)
        # If this is a discovered ticker (not in CSV) we may have a company name
        # carried on the post itself — capture it so the trending entry has metadata.
        discovered_name = post.get("discovered_company_name")
        for ticker in mentioned:
            entry = by_ticker.setdefault(ticker, {
                "mentions": 0,
                "sum_sentiment": 0.0,
                "sum_upvote_ratio": 0.0,
                "post_links": [],
                "discovered_company_name": None,
            })
            entry["mentions"] += 1
            entry["sum_sentiment"] += sent_score
            entry["sum_upvote_ratio"] += post["upvote_ratio"]
            entry["post_links"].append(post["permalink"])
            if discovered_name and not entry["discovered_company_name"]:
                entry["discovered_company_name"] = discovered_name

    ticker_meta = {t["ticker"]: t for t in tickers}
    results = []
    for ticker, data in by_ticker.items():
        if data["mentions"] < min_mentions:
            continue
        avg_sent = data["sum_sentiment"] / data["mentions"]
        avg_upvote = data["sum_upvote_ratio"] / data["mentions"]
        trending_score = data["mentions"] * avg_sent
        meta = ticker_meta.get(ticker, {})
        is_discovered = not meta
        company_name = meta.get("company_name") if meta else (data.get("discovered_company_name") or ticker)
        sector = meta.get("sector", "Discovered" if is_discovered else "")
        label = "bullish" if avg_sent > 0.15 else "bearish" if avg_sent < -0.15 else "neutral"
        results.append({
            "ticker": ticker,
            "company_name": company_name,
            "sector": sector,
            "discovered": is_discovered,                  # True = not in reference CSV
            "mentions": data["mentions"],
            "avg_sentiment": round(avg_sent, 3),
            "avg_upvote_ratio": round(avg_upvote, 3),
            "label": label,
            "trending_score": round(trending_score, 3),
            "sample_posts": data["post_links"][:3],
        })

    # Filter to bullish (positive trending) and sort desc
    bullish = [r for r in results if r["trending_score"] > 0]
    bullish.sort(key=lambda r: r["trending_score"], reverse=True)
    return bullish[:top_n], results  # also return full set for debugging


# --- Main ---
def main():
    here = Path(__file__).parent
    started = datetime.now(timezone.utc)
    print(f"Sentiment Service — run started at {started.isoformat()}")

    print("Loading tickers...")
    tickers = load_tickers(here / TICKERS_CSV)
    print(f"  {len(tickers)} tickers loaded from {TICKERS_CSV}")

    print("Building matchers...")
    matchers = build_matchers(tickers)

    print("Fetching subreddits...")
    all_posts = []
    for sub in SUBREDDITS:
        posts = fetch_subreddit(sub, POSTS_PER_SUB)
        print(f"  r/{sub}: {len(posts)} posts fetched")
        all_posts.extend(posts)
        time.sleep(3)  # be polite to Reddit (increased delay for more subreddits)

    # Temporarily disabled Placera fetching due to scraping challenges
    # print("Fetching Placera forums...")
    # for company in PLACERA_COMPANIES[:3]:  # Test with just first 3 companies
    #     posts = fetch_placera_company(company, 10)
    #     print(f"  Placera {company}: {len(posts)} posts fetched")
    #     all_posts.extend(posts)
    #     time.sleep(2)  # be polite to Placera

    # Flashback (f487 - Aktier) — Swedish retail forum, scraped politely
    tickers_lookup = {t["ticker"].upper(): t for t in tickers}
    flashback_posts = fetch_flashback_all(tickers_lookup)
    print(f"  Flashback total: {len(flashback_posts)} posts")
    all_posts.extend(flashback_posts)

    print("Fetching allaaktier hot companies...")
    allaaktier_hot = fetch_allaaktier_hot(max_items=ALLAAKTIER_MAX_HOT)
    print(f"  allaaktier hot companies: {len(allaaktier_hot)} items")

    print(f"Total posts: {len(all_posts)}")

    print(f"Aggregating (window={WINDOW_HOURS}h, min_mentions={MIN_MENTIONS}, top_n={TOP_N})...")
    trending, all_results = aggregate_trending(
        all_posts, matchers, tickers,
        window_hours=WINDOW_HOURS,
        min_mentions=MIN_MENTIONS,
        top_n=TOP_N,
    )

    if trending:
        print(f"\nTop {len(trending)} trending bullish tickers:")
        for i, t in enumerate(trending, 1):
            print(f"  {i}. {t['ticker']:<10} {t['company_name'][:30]:<32} "
                  f"{t['mentions']:>3} mentions | sentiment {t['avg_sentiment']:+.2f} ({t['label']})")
    else:
        print("\nNo bullish trending tickers in this window.")
        if all_results:
            print(f"  ({len(all_results)} ticker(s) had mentions but didn't meet thresholds.)")
    
    # Show all tickers with any mentions for debugging
    if all_results:
        print(f"\nAll tickers with mentions ({len(all_results)} total):")
        for r in sorted(all_results, key=lambda x: x['mentions'], reverse=True):
            print(f"  {r['ticker']:<10} {r['mentions']:>3} mentions | sentiment {r['avg_sentiment']:+.2f} | score {r['trending_score']:+.2f}")

    output = {
        "generated_at": started.isoformat(),
        "subreddits": SUBREDDITS,
        "placera_companies": [],  # Temporarily disabled
        "flashback_forum": FLASHBACK_FORUM_URL,
        "allaaktier_source": ALLAAKTIER_WEB_URL,
        "allaaktier_hot": allaaktier_hot,
        "window_hours": WINDOW_HOURS,
        "min_mentions": MIN_MENTIONS,
        "trending": trending,
        "stats": {
            "total_posts_fetched": len(all_posts),
            "tickers_with_any_mentions": len(all_results),
            "allaaktier_hot_count": len(allaaktier_hot),
        },
    }

    out_path = here / OUTPUT_JSON
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
