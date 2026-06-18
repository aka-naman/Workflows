import asyncio
import logging
from db import SessionLocal, Lead, init_db
from scrapers import WebSearchScraper, GitHubScraper
from verifier import resolve_company_domain, generate_email_hypotheses, check_email_robust

logger = logging.getLogger(__name__)

async def run_pipeline(
    roles: list[str],
    locations: list[str],
    platforms: list[str],
    limit_per_source: int = 15,
    github_token: str = None,
    log_callback = None
):
    """
    Executes the scraping pipeline based on roles, locations, and platforms.
    """
    # Ensure database is initialized
    init_db()
    db = SessionLocal()
    
    # Initialize engines
    web_scraper = WebSearchScraper()
    github_scraper = GitHubScraper(github_token=github_token)
    
    discovered_leads = []
    
    async def log(msg: str):
        logger.info(msg)
        if log_callback:
            await log_callback(msg)

    await log("🚀 Lead Generation Pipeline Started!")
    await log(f"Roles: {', '.join(roles)} | Locations: {', '.join(locations)} | Platforms: {', '.join(platforms)}")

    try:
        # 1. Scraping Phase
        if "linkedin" in platforms:
            await log("🔍 Commencing LinkedIn X-Ray queries via search engines...")
            for role in roles:
                for loc in locations:
                    # Construct search query variations
                    # Version A: General search looking for email strings
                    query_1 = f'site:linkedin.com/in/ "{role}"'
                    if loc:
                        query_1 += f' "{loc}"'
                    query_1 += ' ("@gmail.com" OR "@outlook.com" OR "contact me at" OR "email")'
                    
                    # Run Google Search
                    google_leads = await web_scraper.scrape_google(query_1, num_pages=2, log_callback=log)
                    discovered_leads.extend(google_leads)
                    
                    # Run Bing Search for additional coverage (Bing is less rate-limited)
                    query_2 = f'site:linkedin.com/in/ "{role}"'
                    if loc:
                        query_2 += f' "{loc}"'
                    query_2 += ' "email"'
                    bing_leads = await web_scraper.scrape_bing(query_2, num_pages=2, log_callback=log)
                    discovered_leads.extend(bing_leads)
                    
                    # Throttle between keywords to prevent search blocks
                    await asyncio.sleep(random_delay := 3.0)

        if "github" in platforms:
            await log("🔍 Commencing GitHub queries...")
            for role in roles:
                for loc in locations:
                    git_leads = await github_scraper.search_leads(
                        role=role, 
                        location=loc, 
                        limit=limit_per_source, 
                        log_callback=log
                    )
                    discovered_leads.extend(git_leads)
                    await asyncio.sleep(2.0)

        await log(f"📊 Raw leads gathered: {len(discovered_leads)}. Starting deduplication and database sync...")

        # 2. Database Sync & Deduplication
        unique_leads = {}
        for lead in discovered_leads:
            profile_url = lead.get("profile_url")
            if not profile_url:
                continue
            
            # If lead is already in unique_leads list, merge (prefer lead with email)
            if profile_url in unique_leads:
                if not unique_leads[profile_url].get("email") and lead.get("email"):
                    unique_leads[profile_url] = lead
            else:
                unique_leads[profile_url] = lead

        # 3. Enrichment and Verification Phase
        saved_count = 0
        duplicate_count = 0
        
        await log("⚡ Beginning B2B email enrichment and syntax verification...")
        
        for profile_url, lead_data in unique_leads.items():
            # Check database for existing profile url to avoid duplicates
            existing = db.query(Lead).filter(Lead.profile_url == profile_url).first()
            if existing:
                duplicate_count += 1
                continue
                
            name = lead_data["name"]
            email = lead_data.get("email")
            title = lead_data.get("title")
            company = lead_data.get("company")
            source = lead_data.get("source_platform")
            
            domain = None
            verification_status = "Unverified"
            
            # Scenario A: Email was already scraped from search engine snippets or GitHub
            if email:
                await log(f"Checking validity for scraped email: {email}")
                verification_status = await check_email_robust(email)
                await log(f"↳ Result: {verification_status}")
            
            # Scenario B: No email scraped, but we have Company and Name -> Guess and Validate!
            elif company and company != "Unknown Company":
                await log(f"No email for {name} ({company}). Resolving company domain...")
                domain = await resolve_company_domain(company)
                if domain:
                    await log(f"↳ Domain resolved to: {domain}. Constructing email patterns...")
                    hypotheses = generate_email_hypotheses(name, domain)
                    
                    found_valid_email = False
                    for candidate in hypotheses:
                        await log(f"Checking pattern: {candidate}")
                        status = await check_email_robust(candidate)
                        await log(f"↳ Result: {status}")
                        if status in ["Verified", "Catch-All", "Unverified"]:
                            email = candidate
                            verification_status = status
                            found_valid_email = True
                            break # Found a valid/working configuration, stop checking patterns
                            
                    if not found_valid_email and hypotheses:
                        # Fallback to the first generated pattern if none verified (marked as Unverified)
                        email = hypotheses[0]
                        verification_status = "Unverified"
            
            # Create Database Record
            new_lead = Lead(
                name=name,
                email=email,
                title=title,
                company=company,
                company_domain=domain,
                source_platform=source,
                profile_url=profile_url,
                verification_status=verification_status
            )
            
            try:
                db.add(new_lead)
                db.commit()
                saved_count += 1
                await log(f"✅ Saved Lead: {name} | {email or 'No Email'} | {title} | {company}")
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to save lead: {e}")
                
            # Rate limit/delay between profile processing to avoid IP flags
            await asyncio.sleep(1.0)
            
        await log(f"🎉 Pipeline Completed! Saved {saved_count} new leads. Skipped {duplicate_count} duplicates.")

    except Exception as e:
        import traceback
        err = f"Pipeline execution failed: {e}\n{traceback.format_exc()}"
        logger.error(err)
        await log(f"❌ Error: {e}")
    finally:
        db.close()
        await web_scraper.close()
        await github_scraper.close()
