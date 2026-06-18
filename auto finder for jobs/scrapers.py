import re
import urllib.parse
import random
import asyncio
import logging
from bs4 import BeautifulSoup
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Edge/122.0.0.0"
]

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

def extract_emails(text):
    if not text:
        return []
    return list(set(EMAIL_REGEX.findall(text)))

def parse_linkedin_title(title_text):
    """
    Parses LinkedIn titles which typically look like:
    - Name - Title - Company | LinkedIn
    - Name - Company - Title | LinkedIn
    - Name - Title | LinkedIn
    Returns (name, title, company)
    """
    if not title_text:
        return None, None, None
    
    # Strip "| LinkedIn" or similar
    cleaned = re.sub(r'\s*\|\s*LinkedIn.*$', '', title_text, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*-\s*LinkedIn.*$', '', cleaned, flags=re.IGNORECASE)
    
    parts = [p.strip() for p in cleaned.split('-')]
    
    name = parts[0] if len(parts) > 0 else "Unknown Name"
    title = parts[1] if len(parts) > 1 else None
    company = parts[2] if len(parts) > 2 else None
    
    # Clean up name if it has suffix
    name = re.sub(r',.*$', '', name) # e.g. "John Doe, PhD"
    
    return name, title, company

class WebSearchScraper:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    
    async def close(self):
        await self.client.aclose()

    async def get_headers(self):
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.google.com/"
        }

    async def scrape_google(self, query: str, num_pages: int = 2, log_callback=None):
        leads = []
        for page in range(num_pages):
            start = page * 10
            url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&start={start}"
            
            msg = f"Searching Google: {query} (Page {page+1})"
            logger.info(msg)
            if log_callback:
                await log_callback(msg)
                
            try:
                headers = await self.get_headers()
                response = await self.client.get(url, headers=headers)
                if response.status_code != 200:
                    warn_msg = f"Google returned status code {response.status_code} (Rate limited / CAPTCHA)"
                    logger.warning(warn_msg)
                    if log_callback:
                        await log_callback(warn_msg)
                    break
                
                soup = BeautifulSoup(response.text, 'html.parser')
                search_results = soup.select('div.g')
                
                if not search_results:
                    # Alternative structure selector
                    search_results = soup.select('div[data-ved]')
                
                for res in search_results:
                    title_el = res.select_one('h3')
                    link_el = res.select_one('a')
                    snippet_el = res.select_one('div[style*="webkit-line-clamp"]') or res.select_one('div.VwiC3b')
                    
                    if not title_el or not link_el:
                        continue
                        
                    title = title_el.get_text()
                    link = link_el['href']
                    snippet = snippet_el.get_text() if snippet_el else ""
                    
                    if "linkedin.com/in/" not in link:
                        continue
                    
                    name, job_title, company = parse_linkedin_title(title)
                    emails = extract_emails(snippet) + extract_emails(title)
                    email = emails[0] if emails else None
                    
                    leads.append({
                        "name": name,
                        "email": email,
                        "title": job_title or "AI / Data Professional",
                        "company": company or "Unknown Company",
                        "profile_url": link,
                        "source_platform": "LinkedIn (Google X-Ray)"
                    })
                
                # Jitter
                await asyncio.sleep(random.uniform(2.0, 5.0))
                
            except Exception as e:
                err_msg = f"Error scraping Google: {e}"
                logger.error(err_msg)
                if log_callback:
                    await log_callback(err_msg)
                await asyncio.sleep(3.0)
                
        return leads

    async def scrape_bing(self, query: str, num_pages: int = 2, log_callback=None):
        leads = []
        for page in range(num_pages):
            first = (page * 10) + 1
            url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&first={first}"
            
            msg = f"Searching Bing: {query} (Page {page+1})"
            logger.info(msg)
            if log_callback:
                await log_callback(msg)
                
            try:
                headers = await self.get_headers()
                response = await self.client.get(url, headers=headers)
                if response.status_code != 200:
                    warn_msg = f"Bing returned status code {response.status_code}"
                    logger.warning(warn_msg)
                    if log_callback:
                        await log_callback(warn_msg)
                    break
                
                soup = BeautifulSoup(response.text, 'html.parser')
                search_results = soup.select('li.b_algo')
                
                for res in search_results:
                    title_el = res.select_one('h2 a')
                    snippet_el = res.select_one('div.b_caption p') or res.select_one('p')
                    
                    if not title_el:
                        continue
                        
                    title = title_el.get_text()
                    link = title_el['href']
                    snippet = snippet_el.get_text() if snippet_el else ""
                    
                    if "linkedin.com/in/" not in link:
                        continue
                    
                    name, job_title, company = parse_linkedin_title(title)
                    emails = extract_emails(snippet) + extract_emails(title)
                    email = emails[0] if emails else None
                    
                    leads.append({
                        "name": name,
                        "email": email,
                        "title": job_title or "AI / Data Professional",
                        "company": company or "Unknown Company",
                        "profile_url": link,
                        "source_platform": "LinkedIn (Bing X-Ray)"
                    })
                
                # Jitter
                await asyncio.sleep(random.uniform(2.0, 4.0))
                
            except Exception as e:
                err_msg = f"Error scraping Bing: {e}"
                logger.error(err_msg)
                if log_callback:
                    await log_callback(err_msg)
                await asyncio.sleep(3.0)
                
        return leads

class GitHubScraper:
    def __init__(self, github_token=None):
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Antigravity-Lead-Scraper"
        }
        if github_token:
            self.headers["Authorization"] = f"token {github_token}"
        self.client = httpx.AsyncClient(headers=self.headers, timeout=15.0)

    async def close(self):
        await self.client.aclose()

    async def fetch_user_email_from_commits(self, username: str):
        """
        Hack: If user profile doesn't show email, inspect their public events/commits
        to extract the email associated with git commits.
        """
        try:
            url = f"https://api.github.com/users/{username}/events/public"
            response = await self.client.get(url)
            if response.status_code == 200:
                events = response.json()
                for event in events:
                    if event.get("type") == "PushEvent":
                        commits = event.get("payload", {}).get("commits", [])
                        for commit in commits:
                            author = commit.get("author", {})
                            email = author.get("email")
                            # Filter out users.noreply.github.com default alias emails
                            if email and "@" in email and "noreply" not in email:
                                return email
        except Exception as e:
            logger.warning(f"Error fetching commits for GitHub user {username}: {e}")
        return None

    async def search_leads(self, role: str, location: str = "", limit: int = 10, log_callback=None):
        leads = []
        # Construct query: e.g. "data engineer location:USA"
        query = f'"{role}"'
        if location:
            query += f' location:"{location}"'
            
        msg = f"Searching GitHub API: {query}"
        logger.info(msg)
        if log_callback:
            await log_callback(msg)
            
        try:
            url = f"https://api.github.com/search/users?q={urllib.parse.quote(query)}&per_page={min(limit, 30)}"
            response = await self.client.get(url)
            if response.status_code == 403:
                rate_msg = "GitHub API rate limit exceeded. Provide an OAuth Token in settings to bypass."
                logger.warning(rate_msg)
                if log_callback:
                    await log_callback(rate_msg)
                return leads
            elif response.status_code != 200:
                err_msg = f"GitHub API returned error: {response.status_code}"
                logger.warning(err_msg)
                if log_callback:
                    await log_callback(err_msg)
                return leads
                
            users = response.json().get("items", [])
            for user in users[:limit]:
                username = user["login"]
                profile_url = user["html_url"]
                
                # Fetch full user details
                user_detail_response = await self.client.get(f"https://api.github.com/users/{username}")
                if user_detail_response.status_code == 200:
                    detail = user_detail_response.json()
                    name = detail.get("name") or username
                    email = detail.get("email")
                    company = detail.get("company") or "GitHub Contributor"
                    bio = detail.get("bio") or ""
                    
                    # Clean company names (remove @ prefix etc)
                    if company and company.startswith("@"):
                        company = company[1:]
                        
                    # If email is not public, search commits
                    if not email:
                        email = await self.fetch_user_email_from_commits(username)
                        
                    leads.append({
                        "name": name,
                        "email": email,
                        "title": f"{role} (GitHub Developer)",
                        "company": company,
                        "profile_url": profile_url,
                        "source_platform": "GitHub"
                    })
                    
                await asyncio.sleep(1.0) # Respect API guidelines
                
        except Exception as e:
            err_msg = f"Error during GitHub search: {e}"
            logger.error(err_msg)
            if log_callback:
                await log_callback(err_msg)
                
        return leads
