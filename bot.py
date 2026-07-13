import logging
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from urllib.parse import urljoin
import datetime
import pytz
import os
from dateutil import parser as date_parser

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Use a standard browser User-Agent so the sites don't block the request
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
}

def fetch_html(url):
    """Fetches the HTML content of a given URL."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logging.error(f"Failed to fetch {url}: {e}")
        return None

def extract_publication_date(soup, fallback_date=None):
    """
    Extracts the publication date from common HTML meta tags and attributes.
    Returns a timezone-aware datetime object, or fallback_date if not found.
    """
    date_selectors = [
        ('meta[property="article:published_time"]', 'content'),
        ('meta[name="publish_date"]', 'content'),
        ('meta[name="date"]', 'content'),
        ('meta[name="DC.date"]', 'content'),
        ('time[datetime]', 'datetime'),
        ('span[class*="date"]', None),
        ('div[class*="date"]', None),
    ]
    
    for selector, attr in date_selectors:
        element = soup.select_one(selector)
        if element:
            date_str = element.get(attr) if attr else element.get_text(strip=True)
            if date_str:
                try:
                    parsed_date = date_parser.parse(date_str)
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=pytz.UTC)
                    return parsed_date
                except (ValueError, TypeError):
                    continue
    
    return fallback_date

def extract_article_context(url):
    """
    Dives into an individual article link and extracts a rich text summary.
    Also returns the publication date if found.
    """
    logging.info(f"Diving into article for rich context: {url}")
    html = fetch_html(url)
    if not html:
        return "Could not fetch article context.", None
        
    soup = BeautifulSoup(html, "html.parser")
    pub_date = extract_publication_date(soup)
    paragraphs = soup.find_all("p")
    
    valid_paragraphs = []
    for p in paragraphs:
        text = p.get_text(strip=True)
        if len(text) > 70: 
            valid_paragraphs.append(text)
            
    context_text = "\n\n".join(valid_paragraphs[:4])
    if not context_text.strip():
        context_text = " ".join([p.get_text(strip=True) for p in paragraphs[:3]])

    return (context_text if context_text.strip() else "No detailed text context found on page."), pub_date

def scrape_anthropic_announcements():
    """Scrapes Anthropic newsroom, correctly associating dates from the index layout."""
    base_url = "https://www.anthropic.com"
    news_url = f"{base_url}/news"
    logging.info(f"Scraping Anthropic news announcements at {news_url}...")
    
    html = fetch_html(news_url)
    items = []
    if not html:
        return items
        
    soup = BeautifulSoup(html, "html.parser")
    today_utc = datetime.datetime.now(pytz.UTC).date()
    seen_urls = set()
    
    for article in soup.find_all("a", href=True):
        href = article['href']
        if "/news/" in href and href not in seen_urls:
            full_url = urljoin(base_url, href)
            
            container = article.find_parent(class_=lambda c: c and "post" in c.lower()) or article.find_parent()
            container_text = container.get_text(" ", strip=True) if container else ""
            
            try:
                parsed_date = date_parser.parse(container_text, fuzzy=True)
                if parsed_date.date() != today_utc:
                    continue
            except (ValueError, TypeError):
                continue

            seen_urls.add(href)
            title = article.get_text(strip=True) or "Anthropic Update"
            if len(title) < 5: 
                continue 
                
            context, deep_pub_date = extract_article_context(full_url)
            final_date = deep_pub_date if deep_pub_date else parsed_date
            if final_date.tzinfo is None:
                final_date = final_date.replace(tzinfo=pytz.UTC)

            items.append({
                "title": f"Anthropic: {title}",
                "link": full_url,
                "description": context,
                "pubDate": final_date
            })
                
    return items

def scrape_meta_announcements():
    """Scrapes the Meta newsroom page and skips deep dives for older stories."""
    base_url = "https://about.fb.com"
    news_url = f"{base_url}/news/"
    logging.info(f"Scraping Meta news announcements at {news_url}...")
    
    html = fetch_html(news_url)
    items = []
    if not html:
        return items
        
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.find_all("article") 
    today_utc = datetime.datetime.now(pytz.UTC).date()
    
    if not articles:
        articles = soup.find_all("div", class_=lambda c: c and "post" in c.lower())

    for article in articles: 
        date_tag = article.find("time") or article.find(class_=lambda c: c and "date" in c.lower())
        if date_tag:
            try:
                parsed_date = date_parser.parse(date_tag.get_text(strip=True))
                if parsed_date.date() != today_utc:
                    continue
            except (ValueError, TypeError):
                pass

        link_tag = article.find("a", href=True)
        if not link_tag:
            continue
            
        full_url = urljoin(base_url, link_tag["href"])
        title = link_tag.get_text(strip=True) or "Meta Update"
        
        if len(title) < 5:
            continue
            
        context, deep_pub_date = extract_article_context(full_url)
        final_date = deep_pub_date if deep_pub_date else (date_parser.parse(date_tag.get_text(strip=True)) if date_tag else None)
        
        if final_date and final_date.date() == today_utc:
            if final_date.tzinfo is None:
                final_date = final_date.replace(tzinfo=pytz.UTC)
            items.append({
                "title": f"Meta: {title}",
                "link": full_url,
                "description": context,
                "pubDate": final_date
            })
         
    return items

def scrape_perplexity_announcements():
    """Scrapes the strict Perplexity blog page using index dates to filter."""
    base_url = "https://www.perplexity.ai"
    news_url = "https://www.perplexity.ai/hub/blog"
    logging.info(f"Scraping Perplexity announcements at {news_url}...")
    
    html = fetch_html(news_url)
    items = []
    if not html:
        return items
        
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.find_all("a", href=True)
    seen_urls = set()
    today_utc = datetime.datetime.now(pytz.UTC).date()
    
    for article in articles:
        href = article['href']
        if "/hub/" in href and href not in seen_urls and href != "/hub/blog":
            
            parent_text = article.find_parent().get_text(" ", strip=True) if article.find_parent() else ""
            try:
                parsed_date = date_parser.parse(parent_text, fuzzy=True)
                if parsed_date.date() != today_utc:
                    continue
            except (ValueError, TypeError):
                continue

            seen_urls.add(href)
            full_url = urljoin(base_url, href)
            title = article.get_text(strip=True)
            if len(title) < 5: 
                continue 
                
            context, deep_pub_date = extract_article_context(full_url)
            final_date = deep_pub_date if deep_pub_date else parsed_date
            if final_date.tzinfo is None:
                final_date = final_date.replace(tzinfo=pytz.UTC)
            
            items.append({
                "title": f"Perplexity: {title}",
                "link": full_url,
                "description": context,
                "pubDate": final_date
            })
                 
    return items

def clear_rss_feed(output_file="market_intel_rss.xml"):
    """Removes the existing RSS feed file before generating a new one."""
    if os.path.exists(output_file):
        try:
            os.remove(output_file)
            logging.info(f"Cleared existing RSS feed file: {output_file}")
        except Exception as e:
            logging.error(f"Failed to clear RSS feed file: {e}")

def build_rss_feed(feed_items, output_file="market_intel_rss.xml"):
    """Takes the scraped items and compiles them into a valid RSS XML file."""
    logging.info("Building RSS feed...")
    fg = FeedGenerator()
    fg.title("KI Market Intel - Tech News")
    fg.link(href="https://github.com/YOUR-USERNAME/ki-market-intel", rel="alternate")
    fg.description("Automated market intelligence gathering for Anthropic, Meta, and Perplexity announcements.")
    fg.language("en")

    for data in feed_items:
        fe = fg.add_entry()
        fe.title(data["title"])
        fe.link(href=data["link"])
        fe.description(data["description"])
        fe.published(data["pubDate"])

    fg.rss_file(output_file, pretty=True)
    logging.info(f"RSS feed successfully written to {output_file}")

def main():
    logging.info("KI Market Intel Bot initialized.")
    clear_rss_feed()
    
    try:
        anthropic_intel = scrape_anthropic_announcements()
        meta_intel = scrape_meta_announcements()
        perplexity_intel = scrape_perplexity_announcements()
        
        all_intel = anthropic_intel + meta_intel + perplexity_intel
        
        # --- CHANGED: Always compile the feed file ---
        if not all_intel:
            logging.warning("No intelligence data gathered for today's date. Generating empty feed file.")
        
        build_rss_feed(all_intel)
            
    except Exception as e:
        logging.error(f"An error occurred during execution: {e}")

if __name__ == "__main__":
    main()