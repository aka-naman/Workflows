# LeadGen AI: Contact Scraping & Verification Pipeline

An advanced, asynchronous pipeline written in Python 3.12 that scrapes the internet for professional contact details (Name, Email, Title, Company) targeting roles in AI, Data Analytics, and Data Engineering.

## Features

1. **Dual Search X-Ray Engines**: Searches LinkedIn profiles via Google and Bing using advanced dorks (e.g. `site:linkedin.com/in/ "data analyst" "manager"`).
2. **GitHub Developer Engine**: Searches GitHub bios and public profiles. Extracts emails using a git commit inspection hack if public emails are hidden.
3. **B2B Domain Resolver**: Identifies target company domains automatically via official search results (e.g., "Google" -> `google.com`).
4. **Email Constructor**: Formulates standard B2B patterns (like `first.last@company.com`, `first_initial+last@company.com`) from name and domain.
5. **SMTP Validation & DNS MX Lookup**: Validates emails using Google DNS-over-HTTPS and SMTP pings on Port 25. Detects "Catch-All" mailboxes.
6. **Port 25 Firewall Fallback**: If your local network/ISP blocks outgoing SMTP port 25, the pipeline gracefully marks syntax-valid, MX-resolving domains as `Unverified` rather than throwing false failures.
7. **Premium Web Dashboard**: A modern glassmorphic dashboard to run campaigns, configure keywords, view stats, monitor raw terminal logs via WebSockets, search/filter leads, and export them.
8. **Excel Exporter**: Downloads leads in the exact requested format: `SNo`, `Name`, `Email`, `Title`, `Company`.

---

## Folder Structure

```
├── db.py                # Database models and session setup (SQLite)
├── scrapers.py          # Search engines & GitHub scraping logic
├── verifier.py          # Domain resolving & SMTP/MX email verification
├── pipeline.py          # Orchestration pipeline connecting scrapers & verifier
├── app.py               # FastAPI server endpoints (WebSockets, CSV, SQLite APIs)
├── main.py              # Launcher script (starts web server)
├── templates/
│   └── index.html       # Single-page glassmorphism frontend dashboard
└── requirements.txt     # Python dependencies
```

---

## How to Run

1. **Install Dependencies**
   Ensure Python 3.12+ is installed. Run:
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the Web Application**
   Run the launcher script:
   ```bash
   python main.py
   ```

3. **Access the Dashboard**
   Open your browser and navigate to:
   [http://127.0.0.1:8000](http://127.0.0.1:8000)

4. **Run a Campaign**
   - Enter your target Job Roles (e.g., "Machine Learning Lead", "Data Engineer", "HR Specialist").
   - Set locations (e.g., "San Francisco", "London", "Remote").
   - Press **Run Scraper Pipeline** to start the crawl.
   - Watch logs stream in real-time in the Console.
   - Once completed, click **Export Excel** to download the `scraped_leads.xlsx` file matching your custom headers!
