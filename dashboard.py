#!/usr/bin/env python3
"""
Sentiment Dashboard — Interactive visualization of stock sentiment data
"""

import json
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime

# Page config
st.set_page_config(
    page_title="Stock Sentiment Dashboard",
    page_icon="📈",
    layout="wide"
)

# Load data
@st.cache_data
def load_sentiment_data():
    """Load the latest sentiment data from trending.json"""
    try:
        with open("trending.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        st.error("trending.json not found. Please run sentiment_service.py first.")
        return None

def main():
    st.title("📈 Stock Sentiment Dashboard")
    st.markdown("Real-time sentiment analysis from Reddit investing communities")

    # Load data
    data = load_sentiment_data()
    if not data:
        return

    # Header info
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Posts Analyzed", data["stats"]["total_posts_fetched"])
    with col2:
        st.metric("Subreddits Monitored", len(data["subreddits"]))
    with col3:
        st.metric("Tickers with Mentions", data["stats"]["tickers_with_any_mentions"])
    with col4:
        st.metric("Analysis Window", f"{data['window_hours']}h")

    st.markdown("---")

    # Trending tickers section
    st.header("🚀 Top Trending Bullish Stocks")

    if data["trending"]:
        # Convert to DataFrame for easier manipulation
        df_trending = pd.DataFrame(data["trending"])

        # Display top 5 in a nice format
        cols = st.columns(min(5, len(df_trending)))
        for i, (_, row) in enumerate(df_trending.head().iterrows()):
            with cols[i]:
                sentiment_color = "🟢" if row["avg_sentiment"] > 0.2 else "🟡" if row["avg_sentiment"] > 0 else "🔴"
                st.metric(
                    f"{row['ticker']}",
                    f"{row['mentions']} mentions",
                    f"{row['avg_sentiment']:+.2f} sentiment",
                    delta_color="normal"
                )
                st.caption(f"{row['company_name'][:25]}...")
                st.caption(f"{sentiment_color} {row['label'].title()}")

        st.markdown("---")

        # Detailed table
        st.subheader("📊 Detailed Trending Analysis")
        display_df = df_trending[[
            "ticker", "company_name", "sector", "mentions", "velocity_mentions_per_day",
            "avg_sentiment", "avg_upvote_ratio", "label", "trending_score"
        ]].copy()

        # Format columns
        display_df["avg_sentiment"] = display_df["avg_sentiment"].round(3)
        display_df["avg_upvote_ratio"] = display_df["avg_upvote_ratio"].round(3)
        display_df["trending_score"] = display_df["trending_score"].round(3)

        # Color code sentiment
        def color_sentiment(val):
            if val > 0.2:
                return 'background-color: #d4edda; color: #155724'
            elif val > 0:
                return 'background-color: #fff3cd; color: #856404'
            else:
                return 'background-color: #f8d7da; color: #721c24'

        styled_df = display_df.style.apply(
            lambda x: [color_sentiment(v) if i == 4 else '' for i, v in enumerate(x)],
            axis=1
        )

        st.dataframe(styled_df, use_container_width=True)

        # Charts section
        st.markdown("---")
        st.header("📈 Sentiment Analysis Charts")

        col1, col2 = st.columns(2)

        with col1:
            # Sentiment vs Mentions scatter plot
            fig_scatter = px.scatter(
                df_trending,
                x="mentions",
                y="avg_sentiment",
                size="trending_score",
                color="sector",
                hover_name="ticker",
                title="Sentiment vs Mentions by Sector",
                labels={"mentions": "Number of Mentions", "avg_sentiment": "Average Sentiment"}
            )
            fig_scatter.add_hline(y=0.2, line_dash="dash", line_color="green",
                                annotation_text="Bullish Threshold")
            fig_scatter.add_hline(y=-0.2, line_dash="dash", line_color="red",
                                annotation_text="Bearish Threshold")
            st.plotly_chart(fig_scatter, use_container_width=True)

        with col2:
            # Trending score bar chart
            fig_bar = px.bar(
                df_trending.head(10),
                x="ticker",
                y="trending_score",
                color="avg_sentiment",
                title="Top 10 Trending Scores",
                labels={"trending_score": "Trending Score", "ticker": "Ticker"}
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        # Sector analysis
        st.subheader("🏢 Sector Analysis")
        sector_stats = df_trending.groupby("sector").agg({
            "mentions": "sum",
            "avg_sentiment": "mean",
            "trending_score": "sum"
        }).round(3).reset_index()

        col1, col2 = st.columns(2)
        with col1:
            fig_sector_mentions = px.pie(
                sector_stats,
                values="mentions",
                names="sector",
                title="Mentions by Sector"
            )
            st.plotly_chart(fig_sector_mentions, use_container_width=True)

        with col2:
            fig_sector_sentiment = px.bar(
                sector_stats,
                x="sector",
                y="avg_sentiment",
                title="Average Sentiment by Sector",
                color="avg_sentiment",
                color_continuous_scale=["red", "yellow", "green"]
            )
            st.plotly_chart(fig_sector_sentiment, use_container_width=True)

    else:
        st.warning("No trending bullish tickers found in the current analysis window.")

    # Hot signal section from allaaktier
    if data.get("allaaktier_hot"):
        st.markdown("---")
        st.header("🔥 Hot Companies from allaaktier.se")
        df_hot = pd.DataFrame(data["allaaktier_hot"])
        if not df_hot.empty:
            if "trend7d" in df_hot.columns:
                df_hot["trend7d"] = pd.to_numeric(df_hot["trend7d"], errors="coerce")
                df_hot = df_hot.sort_values("trend7d", ascending=False)

            display_hot_df = df_hot[[
                "ticker", "company_name", "trend7d", "discord_members",
                "facebook_members", "owner_count", "market_cap", "detail_url"
            ]].copy()
            display_hot_df["detail_url"] = display_hot_df["detail_url"].apply(
                lambda u: f"[View details]({u})"
            )
            st.write(display_hot_df.to_markdown(index=False), unsafe_allow_html=True)
        else:
            st.info("No hot company data available from allaaktier right now.")

    # All mentions section
    if data["trending"]:
        st.markdown("---")
        st.header("📋 All Ticker Mentions")

        df_all = pd.DataFrame(data["trending"])
        display_all_df = df_all[[
            "ticker", "company_name", "sector", "mentions",
            "avg_sentiment", "avg_upvote_ratio", "label", "trending_score"
        ]].copy()

        display_all_df["avg_sentiment"] = display_all_df["avg_sentiment"].round(3)
        display_all_df["avg_upvote_ratio"] = display_all_df["avg_upvote_ratio"].round(3)
        display_all_df["trending_score"] = display_all_df["trending_score"].round(3)

        # Sort by mentions
        display_all_df = display_all_df.sort_values("mentions", ascending=False)

        st.dataframe(display_all_df, use_container_width=True)

    # Footer
    st.markdown("---")
    st.caption(f"Last updated: {data['generated_at']}")
    st.caption("Data sourced from Reddit communities: " + ", ".join(data["subreddits"]))

if __name__ == "__main__":
    main()