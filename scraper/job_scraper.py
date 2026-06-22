# ("AI/ML" OR "AI Engineer" OR "Data Analytics" OR "Software Engineer") ("fresher" OR "entry level" OR "0-1 years") ("India" OR "Remote") after:2026-06-21
import os
import re
import time
import urllib.parse
from bs4 import BeautifulSoup
import pandas as pd
from playwright.sync_api import sync_playwright

# Try importing ollama for smart parsing; fall back to regex if not available
try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

# Configuration
USE_OLLAMA = HAS_OLLAMA     # Set to False if you don't want to use local LLM
OLLAMA_MODEL = "llama3.2"     # Change to gemma2, qwen2.5, etc., as desired
HEADLESS = False             # Run browser in visible mode to help solve CAPTCHAs
TARGET_RESULTS = 40          # Total unique job listings to fetch
TIME_LIMIT = "d2"            # "d2" = last 2 days, "w1" = last week, None = no limit

def clean_text(text):
    """Clean extra whitespace and formatting."""
    return re.sub(r'\s+', ' ', text).strip()

def normalize_url(url):
    """Strip URL fragments and query parameters to avoid duplicate crawling."""
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

def extract_emails(text):
    """Find emails using regex."""
    if not text:
        return []
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(email_pattern, text)
    # Remove duplicates and common false positives
    valid_emails = list(set(e.lower() for e in emails if not e.endswith(('.png', '.jpg', '.jpeg', '.gif', '.pdf', '.webp', '.svg'))))
    return valid_emails

def extract_role_from_snippet(snippet):
    """Extract a cleaned job role from text snippet using pattern matching."""
    cleaned = re.sub(r'[^\w\s\-\/\(\)\#\.\+]', '', snippet).strip()
    
    # Common job title patterns
    role_indicators = [
        r"(?:hiring|recruiting|openings? for)\s+(?:a\s+|an\s+)?([A-Za-z0-9\s\-\/\(\)\#\.\+]{3,40})",
        r"(?:role|position|vacancy)\s*:\s*([A-Za-z0-9\s\-\/\(\)\#\.\+]{3,40})",
        r"([A-Za-z0-9\s\-\/\(\)\#\.\+]{3,40})\s+(?:role|position|vacancy|opening)"
    ]
    
    for pattern in role_indicators:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            if len(extracted) > 3 and not any(w in extracted.lower() for w in ["alert", "urgent", "immediate", "opportunity", "freshers"]):
                return extracted
                
    # Fallback to key terms
    job_keywords = ["Software Engineer", "Developer", "Analyst", "Intern", "Associate", "QA", "Tester", "AI", "ML", "Data Engineer", "Frontend", "Backend", "Fullstack", "Product Manager"]
    for kw in job_keywords:
        match = re.search(rf"\b[A-Za-z\s\-]*{kw}[A-Za-z\s\-]*\b", cleaned, re.IGNORECASE)
        if match:
            return match.group(0).strip()
            
    # Default to first part of snippet
    return snippet[:50] + "..." if len(snippet) > 50 else snippet

def parse_linkedin_title(title):
    """Parse LinkedIn title to extract poster name and basic job role."""
    parts = [p.strip() for p in title.split("|")]
    posted_by = "Not Found"
    role = "Not Found"
    
    # LinkedIn post titles often contain '| Name | Comments Count' or '| Name'
    if len(parts) >= 2:
        for part in parts:
            if "comments" not in part.lower() and "hiring" not in part.lower() and "jobs" not in part.lower() and "linkedin" not in part.lower() and part != "":
                words = part.split()
                if 1 <= len(words) <= 4: # Typical name length
                    posted_by = part
                    break
        role = extract_role_from_snippet(parts[0])
    else:
        # Check format: "Name on LinkedIn: Post description"
        match = re.search(r"^(.+?) on LinkedIn:", title)
        if match:
            posted_by = match.group(1).strip()
            role = extract_role_from_snippet(title.replace(match.group(0), "").strip())
            
    return posted_by, role

def search_google_paginated(playwright, query, target_count=40):
    """Search Google with pagination to gather target_count unique organic links."""
    print(f"[*] Starting Google Search pagination for: '{query}'")
    browser = playwright.chromium.launch(headless=HEADLESS)
    
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800}
    )
    page = context.new_page()
    
    unique_items = {} # key: normalized url, value: dict of details
    start = 0
    consecutive_empty_pages = 0
    
    while len(unique_items) < target_count and start < 100:
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://www.google.com/search?q={encoded_query}&start={start}&num=10"
        if TIME_LIMIT:
            google_url += f"&tbs=qdr:{TIME_LIMIT}"
            
        print(f"[*] Crawling search page: start={start}...")
        try:
            page.goto(google_url, timeout=30000)
            
            # Check for Cookie Consent
            if "consent.google.com" in page.url or page.locator("button:has-text('Accept all')").is_visible() or page.locator("button:has-text('I agree')").is_visible():
                print("[*] Bypassing Google Cookie Consent...")
                for selector in ["button:has-text('Accept all')", "button:has-text('I agree')", "button:has-text('Agree')"]:
                    try:
                        button = page.locator(selector)
                        if button.is_visible():
                            button.click()
                            page.wait_for_load_state("networkidle")
                            break
                    except Exception:
                        pass

            # Check for CAPTCHA
            if "unusual traffic" in page.content().lower() or page.locator("#captcha-form").is_visible():
                print("[!] CAPTCHA detected!")
                page.screenshot(path="google_captcha.png")
                if HEADLESS:
                    raise RuntimeError("CAPTCHA triggered. Set HEADLESS = False in job_scraper.py to solve it manually.")
                else:
                    print("[*] Please solve the CAPTCHA in the open browser window now...")
                    page.wait_for_selector("#search", timeout=120000)
            
            # Wait for search container
            page.wait_for_selector("#search", timeout=15000)
            
            # Parse search result blocks
            blocks = page.locator("div.g, div.MjjYud").all()
            new_additions = 0
            
            for block in blocks:
                link_el = block.locator("a[href]").first
                if not link_el or not link_el.is_visible():
                    continue
                href = link_el.get_attribute("href")
                if not href or not href.startswith("http") or "google.com" in href:
                    continue
                    
                norm_url = normalize_url(href)
                if norm_url in unique_items:
                    continue
                    
                # Title
                title_el = block.locator("h3").first
                title = title_el.inner_text() if title_el and title_el.is_visible() else link_el.inner_text()
                
                # Snippet
                snippet = ""
                for selector in ["div.VwiC3b", "span.aCOpbc", "div[style*='-webkit-line-clamp']"]:
                    snip_el = block.locator(selector).first
                    if snip_el and snip_el.is_visible():
                        snippet = snip_el.inner_text()
                        break
                
                unique_items[norm_url] = {
                    "url": norm_url,
                    "title": title,
                    "snippet": snippet
                }
                new_additions += 1
                
            print(f"[+] Found {new_additions} new unique URLs on this page. (Total gathered: {len(unique_items)})")
            
            if new_additions == 0:
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 2:
                    print("[*] No more new results found. Exiting pagination loop.")
                    break
            else:
                consecutive_empty_pages = 0
                
            start += 10
            time.sleep(2) # Avoid aggressive scraping block
            
        except Exception as e:
            print(f"[!] Error on search page: {e}")
            page.screenshot(path="google_search_error.png")
            break
            
    browser.close()
    return list(unique_items.values())

def extract_company_from_text(text):
    """Extract company name using heuristics from text."""
    if not text:
        return "Not Found"
    # Look for common patterns
    patterns = [
        r"(?:company|organization)\s*:\s*([A-Za-z0-9\s\&]+?)(?:\n|\r|\||\-|\btags\b|\babout\b|\bjobs?\b|$)",
        r"(?:hiring\s+for|hiring\s+at)\s+([A-Z][A-Za-z0-9\s\&]+?)(?:\b|\n|\r|\||\-|$)"
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            val = match.group(1).strip()
            if 2 < len(val) < 40 and not any(w in val.lower() for w in ["fresher", "remote", "bangalore", "india", "opportunity", "post", "jobs", "hiring", "us", "we"]):
                return val
    return "Not Found"

def extract_company(url, title, page_text, snippet):
    """Combine heuristics to determine the company name."""
    # Check LinkedIn site pattern in title
    match = re.search(r"([A-Za-z0-9\&\s]+?)'s\s+global\s+career\s+site", title, re.IGNORECASE)
    if match:
        return match.group(1).strip()
        
    combined = title + " " + snippet + " " + page_text
    h_company = extract_company_from_text(combined)
    if h_company != "Not Found":
        return h_company
        
    # Check for hashtag companies
    hashtags = re.findall(r"#([a-zA-Z0-9]+)", combined)
    for tag in hashtags:
        if tag.lower() not in ["hiring", "jobs", "freshers", "fresher", "recruiting", "recruitment", "careers", "job", "remote", "intern", "engineer", "developer", "software"]:
            return tag.capitalize()
            
    return "Not Found"

def extract_with_llm(page_text):
    """Use Ollama to extract structured details from text."""
    prompt = f"""
Analyze the following webpage content and extract job posting details. 
Look specifically for posts open for freshers / entry-level candidates.

Webpage Content:
\"\"\"
{page_text[:4000]}
\"\"\"

Extract the following fields in strict JSON format. Do not write any explanations.
Json structure:
{{
  "is_fresher_job": true/false,
  "role": "Job title / role name (or 'Not Found')",
  "posted_by": "Name of the person who posted it (or 'Not Found')",
  "email": "Email address of the poster (or 'Not Found')",
  "company": "Company name (or 'Not Found')"
}}
"""
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {
                    'role': 'user',
                    'content': prompt,
                }
            ],
            format='json'
        )
        content = response['message']['content'].strip()
        
        # Find the first '{' and last '}' to isolate the JSON object
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end != -1:
            content = content[start:end+1]
            
        import json
        return json.loads(content)
    except Exception as e:
        print(f"[!] Ollama extraction failed: {e}. Falling back to heuristics.")
        return None

def process_url(playwright, item):
    """Visit a target URL and scrape its content, integrating snippet fallbacks."""
    url = item["url"]
    snippet = item["snippet"]
    title = item["title"]
    
    print(f"\n[*] Crawling: {url}")
    browser = playwright.chromium.launch(headless=HEADLESS)
    
    # Abort media/stylesheets to speed up loading
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    
    # Speed optimization: Abort images, stylesheets, media, and fonts
    page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "stylesheet", "media", "font"] else route.continue_())
    
    html = ""
    page_text = ""
    
    try:
        page.goto(url, timeout=12000)
        page.wait_for_load_state("domcontentloaded")
        html = page.content()
        page_text = page.locator("body").inner_text()
    except Exception as e:
        print(f"[!] Warning: Could not fully crawl page text: {e}. Relying on fallback snippets.")
        
    browser.close()
    
    # Parse poster name and role using LinkedIn specific format
    posted_by = "Not Found"
    role = "Not Found"
    if "linkedin.com/posts/" in url or "linkedin.com/feed/" in url:
        posted_by, role = parse_linkedin_title(title)
        
    # Gather all emails from both page text and search snippets
    combined_emails = extract_emails(page_text) + extract_emails(snippet)
    email_str = ", ".join(list(set(combined_emails))) if combined_emails else "Not Found"
    
    # Extract company name using heuristics as baseline
    company = extract_company(url, title, page_text, snippet)

    # If the page loaded and we are using local LLM
    if page_text and USE_OLLAMA:
        llm_data = extract_with_llm(page_text)
        if llm_data:
            # Overwrite parameters if found by LLM
            if llm_data.get("role") and llm_data["role"] != "Not Found":
                role = llm_data["role"]
            if llm_data.get("posted_by") and llm_data["posted_by"] != "Not Found":
                posted_by = llm_data["posted_by"]
            if llm_data.get("email") and llm_data["email"] != "Not Found":
                email_str = llm_data["email"]
            if llm_data.get("company") and llm_data["company"] != "Not Found":
                company = llm_data["company"]
            is_fresher = llm_data.get("is_fresher_job", False)
            
            return {
                "url": url,
                "role": role,
                "posted_by": posted_by,
                "email": email_str,
                "company": company,
                "is_fresher_job": is_fresher
            }

    # Heuristic Parser
    # Fresher check from combined text/snippet
    fresher_keywords = ["fresher", "entry level", "graduate", "0-1 years", "no experience", "0 years", "2026", "2025"]
    search_context = (page_text + " " + snippet + " " + title).lower()
    is_fresher = any(kw in search_context for kw in fresher_keywords)
    
    # Clean up role defaults
    if role == "Not Found":
        role = extract_role_from_snippet(title if len(title) > 10 else snippet)
        
    return {
        "url": url,
        "role": clean_text(role),
        "posted_by": clean_text(posted_by),
        "email": email_str,
        "company": company,
        "is_fresher_job": is_fresher
    }

def main():
    query = input("Enter your search query for jobs (e.g., 'python developer fresher hiring email'): ").strip()
    if not query:
        print("Query cannot be empty.")
        return
        
    global USE_OLLAMA
    if USE_OLLAMA:
        print(f"[i] Using local Ollama model '{OLLAMA_MODEL}' for extraction.")
    else:
        print("[i] Using optimized heuristics. To enable LLM parsing, run Ollama and 'pip install ollama'.")

    with sync_playwright() as playwright:
        # Search Google with loop-based pagination to fetch many results
        search_items = search_google_paginated(playwright, query, target_count=TARGET_RESULTS)
        
        results = []
        for index, item in enumerate(search_items):
            print(f"\n--- Processing listing {index + 1}/{len(search_items)} ---")
            data = process_url(playwright, item)
            results.append(data)
            
        # Write to Excel
        if results:
            df = pd.DataFrame(results)
            
            # Format DataFrame to: SNo, Name, Email, Title, Company
            df.insert(0, "SNo", range(1, len(df) + 1))
            df = df.rename(columns={
                "posted_by": "Name",
                "email": "Email",
                "role": "Title",
                "company": "Company"
            })
            
            cols = ["SNo", "Name", "Email", "Title", "Company"]
            # Keep only the requested columns that exist
            cols = [c for c in cols if c in df.columns]
            df = df[cols]
            
            output_file = "job_results.xlsx"
            df.to_excel(output_file, index=False)
            print(f"\n[+] Scraping complete! Saved {len(results)} results to '{output_file}'")
        else:
            print("\n[!] No results found to write.")

if __name__ == "__main__":
    main()
