import re
import socket
import smtplib
import random
import logging
import asyncio
import httpx
from scrapers import USER_AGENTS

logger = logging.getLogger(__name__)

# Basic syntax validation regex
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

def clean_company_name(name: str) -> str:
    """Removes legal suffixes from company names to improve search queries."""
    if not name:
        return ""
    # Remove things like LLC, Inc., Co., Corp.
    cleaned = re.sub(r'\b(llc|inc|co|corp|corporation|ltd|limited|gmbh|sa|as|pvt)\.?\b', '', name, flags=re.IGNORECASE)
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned)
    return cleaned.strip()

async def resolve_company_domain(company_name: str) -> str:
    """
    Search Bing to find the official domain of the company.
    E.g. "Google" -> "google.com"
    """
    clean_name = clean_company_name(company_name)
    if not clean_name:
        return None
        
    query = f"{clean_name} official website"
    url = f"https://www.bing.com/search?q={urllib_parse_quote(query)}"
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(response.text, 'html.parser')
                # Grab the first few organic links
                links = soup.select('li.b_algo h2 a')
                for l in links:
                    href = l.get('href', '')
                    # Filter out search engines, social networks, etc.
                    if href and not any(x in href for x in ['bing.com', 'google.com', 'linkedin.com', 'twitter.com', 'facebook.com', 'wikipedia.org']):
                        # Extract domain
                        parsed_uri = urllib_parse(href)
                        domain = parsed_uri.netloc
                        if domain.startswith("www."):
                            domain = domain[4:]
                        return domain
    except Exception as e:
        logger.warning(f"Failed resolving domain for {company_name}: {e}")
        
    # Fallback to simple formatting
    fallback = clean_name.lower().replace(" ", "") + ".com"
    return fallback

# Custom helper for urllib parsing since it's a standalone import inside functions
from urllib.parse import quote as urllib_parse_quote, urlparse as urllib_parse

async def get_mx_records(domain: str) -> list:
    """
    Fetch MX records using Google DNS-over-HTTPS (DoH) API.
    Bypasses UDP blockages and external dependencies.
    """
    if not domain:
        return []
    
    url = f"https://dns.google/resolve?name={domain}&type=MX"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                answers = data.get("Answer", [])
                records = []
                for ans in answers:
                    if ans.get("type") == 15:  # MX record type
                        parts = ans.get("data", "").split()
                        if len(parts) >= 2:
                            try:
                                priority = int(parts[0])
                                exchange = parts[1].rstrip(".")
                                records.append((priority, exchange))
                            except ValueError:
                                continue
                # Sort by priority
                records.sort()
                return [r[1] for r in records]
    except Exception as e:
        logger.warning(f"Error resolving MX records for {domain} via DoH: {e}")
    return []

def generate_email_hypotheses(name: str, domain: str) -> list:
    """Generates standard B2B email addresses based on Name and Domain."""
    if not name or not domain:
        return []
        
    # Clean name: lowercase, alpha only, strip accents if any
    cleaned_name = name.lower()
    cleaned_name = re.sub(r'[^a-z\s.-]', '', cleaned_name)
    parts = [p.strip() for p in cleaned_name.split() if p.strip()]
    
    if len(parts) == 0:
        return []
        
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    
    patterns = []
    if last:
        patterns.append(f"{first}.{last}@{domain}")       # john.doe@domain.com
        patterns.append(f"{first}{last}@{domain}")         # johndoe@domain.com
        patterns.append(f"{first[0]}{last}@{domain}")       # jdoe@domain.com
        patterns.append(f"{first}@{domain}")               # john@domain.com
        patterns.append(f"{first[0]}.{last}@{domain}")     # j.doe@domain.com
    else:
        patterns.append(f"{first}@{domain}")               # john@domain.com
        
    return patterns

def verify_email_smtp(email: str, mx_servers: list) -> str:
    """
    Contacts the mail exchange server to verify if the email address exists.
    Returns: 'Verified', 'Invalid', 'Catch-All', or 'Unknown'
    """
    if not mx_servers:
        return "Invalid"
        
    for server in mx_servers[:2]: # Try the top 2 priority servers
        try:
            # Standard B2B mail servers use Port 25
            smtp = smtplib.SMTP(server, port=25, timeout=5.0)
            
            # Send HELO
            code, _ = smtp.helo("gmail.com")
            if code != 250:
                smtp.quit()
                continue
                
            # Send MAIL FROM
            code, _ = smtp.mail("verify@gmail.com")
            if code != 250:
                smtp.quit()
                continue
                
            # Send RCPT TO (testing the actual address)
            code, _ = smtp.rcpt(email)
            
            # Test a random fake address to check if it's a catch-all domain
            domain = email.split("@")[1]
            fake_email = f"fake_user_test_{random.randint(10000, 99999)}@{domain}"
            fake_code, _ = smtp.rcpt(fake_email)
            
            smtp.quit()
            
            if code == 250:
                if fake_code == 250:
                    return "Catch-All"
                return "Verified"
            elif code == 550:
                return "Invalid"
            else:
                return "Unknown"
                
        except Exception as e:
            # Handled below: SMTP connection failed, likely due to Port 25 firewall block.
            logger.debug(f"SMTP validation check failed on {server}: {e}")
            continue
            
    return "Unknown"

async def check_email_robust(email: str) -> str:
    """
    Performs full syntax, DNS MX validation, and SMTP check.
    If Port 25 is blocked locally, we check if the domain exists and has mail servers, 
    returning "Unverified" (but DNS-valid) to prevent false invalidations.
    """
    if not email or not EMAIL_REGEX.match(email):
        return "Invalid"
        
    domain = email.split("@")[1]
    mx_servers = await get_mx_records(domain)
    
    if not mx_servers:
        return "Invalid"
        
    # Run SMTP check in executor since smtplib is blocking
    loop = asyncio.get_event_loop()
    status = await loop.run_in_executor(None, verify_email_smtp, email, mx_servers)
    
    # Port 25 Block Detection: If status comes back 'Unknown', 
    # but the domain has valid mail servers (MX), we label it 'Unverified' rather than 'Invalid'
    if status == "Unknown":
        return "Unverified"
        
    return status
