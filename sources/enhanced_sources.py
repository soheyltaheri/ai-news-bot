# sources/enhanced_sources.py
import json
import httpx
import feedparser
import re
from bs4 import BeautifulSoup
from database.dedup import is_processed
from sources.web_sources import extract_og_image, extract_media_url_from_entry

# Common headers to avoid bot blocking
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

# Key AI-related keywords to filter Hacker News stories (enhancement)
AI_KEYWORDS = [
    r"\bai\b", r"\bllm\b", r"\bgpt\b", r"\bclaude\b", r"\bllama\b", r"\bgemini\b",
    r"\bopenai\b", r"\bdeepseek\b", r"\bmistral\b", r"\bmachine\s+learning\b",
    r"\bdeep\s+learning\b", r"\bneural\b", r"\bartificial\s+intelligence\b",
    r"\btransformer\b", r"\bbard\b", r"\bcopilot\b", r"\bpytorch\b", r"\btensorflow\b",
    r"\bagi\b", r"\brobot\b", r"\brobotics\b", r"\bnlp\b", r"\bcomputer\s+vision\b",
    r"\bdiffusion\b", r"\bmidjourney\b", r"\bstable\s+diffusion\b"
]

def contains_ai_keywords(title: str, url: str = "") -> bool:
    """
    Checks if the title or URL contains any of the AI keywords.
    """
    text_to_check = f"{title} {url}".lower()
    for kw in AI_KEYWORDS:
        if re.search(kw, text_to_check):
            return True
    return False

async def fetch_from_hacker_news_api(limit: int = 30, filter_ai: bool = True):
    """
    Fetches the top stories from Hacker News using the Firebase API.
    Enhanced to fetch more items and optionally filter for AI-related content.
    """
    print(f"🔍 [Hacker News API] Checking top stories (limit={limit}, filter_ai={filter_ai})...")
    news_items = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: Get top stories IDs
            r = await client.get('https://hacker-news.firebaseio.com/v0/topstories.json', headers=HEADERS)
            if r.status_code != 200:
                print(f"❌ [Hacker News API] Failed to fetch top stories list (status {r.status_code})")
                return []
                
            ids = r.json()[:limit * 3]  # Get extra IDs because filtering might discard some
            
            count = 0
            for sid in ids:
                if count >= limit:
                    break
                
                item_id = str(sid)
                unique_id = f"hn_{item_id}"
                
                if is_processed(unique_id):
                    continue
                
                # Fetch detailed story info
                story_req = await client.get(f'https://hacker-news.firebaseio.com/v0/item/{sid}.json', headers=HEADERS)
                if story_req.status_code != 200:
                    continue
                    
                story = story_req.json()
                if not story or 'title' not in story:
                    continue
                
                title = story['title']
                url = story.get('url', f"https://news.ycombinator.com/item?id={sid}")
                
                if filter_ai and not contains_ai_keywords(title, url):
                    continue
                
                # We have a valid AI/ML story or general story if filtering is off
                news_items.append({
                    'id': unique_id,
                    'raw_text': f"Title: {title}\nURL: {url}",
                    'url': url,
                    'source_name': 'Hacker News',
                    'media_url': None
                })
                count += 1
                
            return news_items
    except Exception as e:
        print(f"❌ Error in fetch_from_hacker_news_api: {e}")
        return []

async def fetch_from_reddit_subreddit(subreddit: str, limit: int = 15):
    """
    Generic helper to fetch hot stories from a specific subreddit using its JSON API.
    """
    print(f"🔍 [Reddit r/{subreddit}] Fetching hot stories (limit={limit})...")
    news_items = []
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=HEADERS, follow_redirects=True)
            if r.status_code == 200:
                data = r.json()
                children = data.get('data', {}).get('children', [])
                
                for child in children:
                    post_data = child.get('data', {})
                    if not post_data or post_data.get('stickied'):
                        # Skip pinned/stickied threads (usually FAQs or announcements)
                        continue
                        
                    post_id = post_data.get('id')
                    unique_id = f"reddit_{subreddit}_{post_id}"
                    
                    if is_processed(unique_id):
                        continue
                        
                    title = post_data.get('title', '')
                    selftext = post_data.get('selftext', '')
                    post_url = post_data.get('url', '')
                    permalink = post_data.get('permalink', '')
                    full_url = f"https://www.reddit.com{permalink}"
                    
                    # Try to extract media/image from post
                    media_url = None
                    # Reddit image posts usually have the image URL directly in post_url
                    if post_url and any(post_url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                        media_url = post_url
                    elif 'preview' in post_data and post_data['preview'].get('images'):
                        try:
                            images = post_data['preview']['images']
                            if images and 'source' in images[0]:
                                # Replace amp; to avoid broken links
                                media_url = images[0]['source']['url'].replace('&amp;', '&')
                        except Exception:
                            pass
                            
                    # Construct a rich raw_text containing the title and self text or link
                    raw_text = f"Title: {title}\n"
                    if selftext:
                        # Truncate selftext to keep it reasonable
                        truncated_text = selftext[:1000] + "..." if len(selftext) > 1000 else selftext
                        raw_text += f"Content: {truncated_text}\n"
                    if post_url and not post_url.startswith("https://www.reddit.com"):
                        raw_text += f"Link: {post_url}\n"
                        
                    news_items.append({
                        'id': unique_id,
                        'raw_text': raw_text.strip(),
                        'url': full_url,
                        'source_name': f"Reddit r/{subreddit}",
                        'media_url': media_url
                    })
            else:
                print(f"❌ [Reddit r/{subreddit}] API returned status code {r.status_code}")
    except Exception as e:
        print(f"❌ Error in fetch_from_reddit_subreddit (r/{subreddit}): {e}")
        
    return news_items

async def fetch_from_reddit_ml(limit: int = 15):
    """
    Fetches the latest hot posts from r/MachineLearning.
    """
    return await fetch_from_reddit_subreddit("MachineLearning", limit=limit)

async def fetch_from_reddit_ai(limit: int = 15):
    """
    Fetches the latest hot posts from r/artificial.
    """
    return await fetch_from_reddit_subreddit("artificial", limit=limit)

async def fetch_rss_multiple_feeds(rss_urls: list, limit_per_feed: int = 10):
    """
    Fetches and parses stories from a list of RSS feeds concurrently.
    Extracts media details automatically.
    """
    print(f"🔍 [RSS Feeds] Fetching from {len(rss_urls)} RSS feeds...")
    news_items = []
    
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            feed_title = getattr(feed.feed, 'title', 'RSS News')
            
            for entry in feed.entries[:limit_per_feed]:
                entry_link = getattr(entry, 'link', '')
                if not entry_link:
                    continue
                    
                if is_processed(entry_link):
                    continue
                
                # First extract from RSS fields
                media_url = extract_media_url_from_entry(entry)
                
                # If not found, attempt og:image from the entry link
                if not media_url:
                    media_url = await extract_og_image(entry_link)
                
                entry_title = getattr(entry, 'title', 'No Title')
                entry_summary = getattr(entry, 'summary', '')
                
                # Strip HTML from summary for cleaner raw_text
                soup = BeautifulSoup(entry_summary, 'html.parser')
                clean_summary = soup.get_text()
                
                news_items.append({
                    'id': entry_link,
                    'raw_text': f"Title: {entry_title}\nSummary: {clean_summary}",
                    'url': entry_link,
                    'source_name': feed_title,
                    'media_url': media_url
                })
        except Exception as e:
            print(f"❌ [RSS Feed Error] Failed to process feed {url}: {e}")
            
    return news_items

async def verify_telegram_channel_preview(channel_handle: str) -> bool:
    """
    Verifies if a Telegram channel is active and contains actual message content 
    using the t.me/s/ web preview. Returns True if active, False otherwise.
    """
    clean_handle = channel_handle.strip().replace('@', '').replace('https://t.me/', '')
    url = f"https://t.me/s/{clean_handle}"
    
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers=HEADERS, follow_redirects=True)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                # Find message elements
                messages = soup.find_all(class_='tgme_widget_message_text')
                if len(messages) > 0:
                    return True
            return False
    except Exception as e:
        print(f"⚠️ Warning: Telegram preview verification failed for @{clean_handle}: {e}")
        return False

async def fetch_github_trending_ai(limit: int = 10):
    """
    Scrapes the daily trending Python page and generic trending page on GitHub,
    filters for AI-related keywords, and returns structured items.
    """
    print(f"🔍 [GitHub Trending] Fetching trending repositories (limit={limit})...")
    
    # AI keywords to filter repositories
    ai_keywords = [
        'ai', 'llm', 'gpt', 'ml', 'deepseek', 'neural', 'agent', 
        'model', 'diffusion', 'transformer', 'machine learning', 
        'deep learning', 'openai', 'claude', 'llama', 'gemini'
    ]
    
    urls = [
        "https://github.com/trending/python?since=daily",
        "https://github.com/trending?since=daily"
    ]
    
    items = []
    seen_repos = set()
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for url in urls:
                try:
                    r = await client.get(url, headers=HEADERS)
                    if r.status_code != 200:
                        print(f"⚠️ [GitHub Trending] Failed to fetch {url} (status {r.status_code})")
                        continue
                        
                    soup = BeautifulSoup(r.text, 'html.parser')
                    articles = soup.find_all('article', class_='Box-row')
                    
                    for art in articles:
                        if len(items) >= limit:
                            break
                            
                        # Repository name and owner
                        h2 = art.find('h2', class_='h3')
                        if not h2:
                            continue
                        a = h2.find('a')
                        if not a or 'href' not in a.attrs:
                            continue
                            
                        repo_path = a['href'].strip('/')
                        if repo_path in seen_repos:
                            continue
                            
                        parts = repo_path.split('/')
                        if len(parts) < 2:
                            continue
                        owner, repo = parts[0], parts[1]
                        
                        # Description
                        p = art.find('p', class_='col-9')
                        desc = p.text.strip() if p else ""
                        
                        # Stars
                        stars = "0"
                        meta_div = art.find('div', class_='f6')
                        if meta_div:
                            stars_a = meta_div.find('a', href=lambda href: href and '/stargazers' in href)
                            if stars_a:
                                stars = stars_a.text.strip()
                                
                        # Check AI keywords in name/desc
                        name_desc_lower = f"{owner}/{repo} {desc}".lower()
                        is_ai = any(kw in name_desc_lower for kw in ai_keywords)
                        if not is_ai:
                            continue
                            
                        seen_repos.add(repo_path)
                        repo_url = f"https://github.com/{repo_path}"
                        
                        items.append({
                            'id': f"gh_trend_{owner}_{repo}",
                            'raw_text': f"Repository: {owner}/{repo}\nDescription: {desc}\nStars: {stars}\nURL: {repo_url}",
                            'url': repo_url,
                            'source_name': "GitHub Trending",
                            'media_url': None
                        })
                except Exception as e:
                    print(f"⚠️ [GitHub Trending] Error fetching {url}: {e}")
                    
    except Exception as e:
        print(f"❌ Error in fetch_github_trending_ai: {e}")
        
    return items[:limit]
