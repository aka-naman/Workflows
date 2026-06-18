import io
import asyncio
from fastapi import FastAPI, BackgroundTasks, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import pandas as pd
from typing import List, Optional
from pydantic import BaseModel

from db import init_db, get_db, Lead
from pipeline import run_pipeline

class ScrapeRequest(BaseModel):
    roles: List[str]
    locations: List[str]
    platforms: List[str]
    github_token: Optional[str] = None


app = FastAPI(title="Lead Generation Scraping Pipeline")

# Initialize SQLite database
init_db()

# Log Queue for WebSocket streaming
class LogQueue:
    def __init__(self):
        self.logs = []
        self.subscribers = set()

    async def put(self, message: str):
        self.logs.append(message)
        if len(self.logs) > 500:
            self.logs.pop(0)
        for ws in list(self.subscribers):
            try:
                await ws.send_text(message)
            except Exception:
                self.subscribers.remove(ws)

    def clear(self):
        self.logs.clear()

log_queue = LogQueue()

# Global state to track background scraper task
class PipelineState:
    def __init__(self):
        self.is_running = False
        self.task = None
        self.github_token = None

pipeline_state = PipelineState()

# Main HTML route
@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    log_queue.subscribers.add(websocket)
    # Send historical logs first
    for log_entry in log_queue.logs:
        await websocket.send_text(log_entry)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        log_queue.subscribers.remove(websocket)
    except Exception:
        if websocket in log_queue.subscribers:
            log_queue.subscribers.remove(websocket)

@app.get("/api/status")
async def get_status():
    return {
        "is_running": pipeline_state.is_running
    }

@app.post("/api/scrape")
async def trigger_scrape(
    request: ScrapeRequest,
    limit_per_source: int = 15
):
    if pipeline_state.is_running:
        raise HTTPException(status_code=400, detail="Scraping pipeline is already running.")
        
    pipeline_state.is_running = True
    log_queue.clear()
    
    if request.github_token:
        pipeline_state.github_token = request.github_token

    # Define the background worker
    async def pipeline_worker():
        try:
            await run_pipeline(
                roles=request.roles,
                locations=request.locations,
                platforms=request.platforms,
                limit_per_source=limit_per_source,
                github_token=pipeline_state.github_token,
                log_callback=log_queue.put
            )
        except Exception as e:
            await log_queue.put(f"Fatal Pipeline Error: {e}")
        finally:
            pipeline_state.is_running = False
            
    # Start the task
    pipeline_state.task = asyncio.create_task(pipeline_worker())
    return {"message": "Pipeline started successfully"}

@app.post("/api/stop")
async def stop_scrape():
    if not pipeline_state.is_running or not pipeline_state.task:
        raise HTTPException(status_code=400, detail="No pipeline task is currently running.")
        
    pipeline_state.task.cancel()
    pipeline_state.is_running = False
    await log_queue.put("⚠️ Pipeline stopped by user.")
    return {"message": "Pipeline stopping process initiated."}

@app.get("/api/leads")
async def get_leads(
    db: Session = Depends(get_db),
    search: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
):
    query = db.query(Lead)
    
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (Lead.name.like(search_filter)) |
            (Lead.email.like(search_filter)) |
            (Lead.title.like(search_filter)) |
            (Lead.company.like(search_filter))
        )
        
    if status and status != "All":
        query = query.filter(Lead.verification_status == status)
        
    total = query.count()
    leads = query.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()
    
    return {
        "total": total,
        "leads": leads
    }

@app.delete("/api/leads/clear")
async def clear_leads(db: Session = Depends(get_db)):
    try:
        db.query(Lead).delete()
        db.commit()
        await log_queue.put("🗑️ All leads cleared from database.")
        return {"message": "All leads deleted successfully."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leads/export")
async def export_leads(db: Session = Depends(get_db)):
    leads = db.query(Lead).order_by(Lead.id.asc()).all()
    if not leads:
        raise HTTPException(status_code=404, detail="No leads available to export.")
        
    # Formulate into exact required fields: SNo, Name, Email, Title, Company
    data = []
    for idx, lead in enumerate(leads, start=1):
        data.append({
            "SNo": idx,
            "Name": lead.name,
            "Email": lead.email or "Not Discovered",
            "Title": lead.title or "Professional",
            "Company": lead.company or "N/A"
        })
        
    df = pd.DataFrame(data)
    
    # Write Excel file to BytesIO in-memory stream
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Leads')
        
        # Access the workbook to apply pretty styling and auto-fit widths
        workbook = writer.book
        worksheet = writer.sheets['Leads']
        
        for col in worksheet.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = col[0].column_letter
            worksheet.column_dimensions[col_letter].width = max(max_len + 3, 10)
            
    output.seek(0)
    
    headers = {
        'Content-Disposition': 'attachment; filename="scraped_leads.xlsx"'
    }
    return StreamingResponse(
        output,
        headers=headers,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
