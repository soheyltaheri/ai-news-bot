# AI News Bot

<p align="center">
  <strong>Automated Multi-Source AI News Curator, Summarizer & Telegram Broadcaster</strong>
</p>

<p align="center">
  <a href="https://github.com/soheyltaheri/ai-news-bot/actions/workflows/ci.yml">
    <img src="https://github.com/soheyltaheri/ai-news-bot/actions/workflows/ci.yml/badge.svg" alt="CI Status" />
  </a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg" alt="Python Versions" />
  <img src="https://img.shields.io/badge/tests-81%20passed-brightgreen.svg" alt="Tests" />
  <img src="https://img.shields.io/badge/Telethon-1.35-2CA5E0.svg?logo=telegram&logoColor=white" alt="Telethon" />
  <img src="https://img.shields.io/badge/LLM-OpenAI%20Compatible-green.svg" alt="LLM Engine" />
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License" />
</p>

---

## 📌 Overview

**AI News Bot** is an autonomous intelligence aggregator that continuously monitors, ingests, deduplicates, and synthesizes artificial intelligence news from top-tier research institutions, company announcement feeds, official Telegram channels, and RSS publishers. 

Utilizing an **LLM engine** for smart summarization and translation, it formats news into polished broadcast posts with media attachments and publishes them to Telegram channels with rate-controlled queue pacing.

---

## 🌟 Key Architecture & Features

```
[ Sources Layer ]
  ├── 30+ Official Telegram AI Channels
  ├── Major RSS Feeds (OpenAI, DeepMind, HuggingFace, TechCrunch, Verge)
  └── Web & Company Announcement Scrapers
         │
         ▼
[ Ingestion & Deduplication ]
  ├── Content Hash & Title Similarity Deduplication (SQLite)
  └── Media Ingestion & Local Cache Management
         │
         ▼
[ AI Synthesis Engine ]
  ├── OpenAI-compatible LLM Router
  ├── Automatic Category Classification
  └── Multilingual Summarization & Persian Translation
         │
         ▼
[ Smart Dispatcher & Broadcasting ]
  ├── Rate-Controlled Smart Queue (Cap: 45, Resume: 20)
  └── Telethon Asynchronous Broadcaster to Target Channel
```

- **Multi-Source Aggregation:** Concurrently monitors over 30 leading Telegram channels, tech RSS feeds, and official company blogs.
- **Robust Deduplication Engine:** Persistent SQLite database tracks processed articles to eliminate repeated stories across overlapping sources.
- **AI-Powered Synthesis:** Generates concise summaries, extracts key takeaways, and formats markdown posts with contextual tags and attribution links.
- **Media Backfill & Handling:** Automatically downloads high-resolution graphics/photos with safe cache rotation.
- **Comprehensive Test Suite:** Thoroughly tested with **81 automated test cases** covering deduplication, sources, parsing, and queue dynamics.

---

## 🚀 Quickstart

### 1. Prerequisites
- Python 3.11+
- Telegram API Credentials (`API_ID`, `API_HASH` from [my.telegram.org](https://my.telegram.org))
- OpenAI or compatible LLM API Key

### 2. Installation

```bash
git clone https://github.com/soheyltaheri/ai-news-bot.git
cd ai-news-bot
pip install -r requirements.txt
```

### 3. Configuration

Copy the example environment template:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
API_ID=your_api_id
API_HASH=your_api_hash
DESTINATION_CHANNEL=@your_channel_name
ROUTER_BASE_URL=https://api.openai.com/v1
ROUTER_API_KEY=sk-your-openai-api-key
MODEL_NAME=gpt-4o-mini
```

### 4. Telegram Authentication

Run the interactive login script once to generate your authorized Telethon session:

```bash
python login.py
```

### 5. Running the Aggregator

Start the continuous aggregator and broadcast daemon:

```bash
python main.py
```

---

## 🧪 Running Automated Tests

Run the full pytest suite:

```bash
pytest -v
```

---

## 📄 License

This repository is distributed under the [MIT License](LICENSE).
