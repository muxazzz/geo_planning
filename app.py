from __future__ import annotations

from io import StringIO

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from algorithm import plan_routes_algorithm_3, read_points_file


app = FastAPI(title="Геопланирование")
app.mount("/static", StaticFiles(directory="static"), name="static")

LAST_RESULT: dict | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    with open("static/index.html", encoding="utf-8") as file:
        return file.read()


@app.post("/api/plan")
async def plan(
    file: UploadFile = File(...),
    n_managers: str = Form(""),
    max_visits_per_day: int = Form(12),
    n_working_days: int = Form(22),
) -> dict:
    global LAST_RESULT

    try:
        content = await file.read()
        source = read_points_file(file.filename or "", content)
        parsed_n_managers = int(n_managers) if n_managers.strip() else None
        result = plan_routes_algorithm_3(
            source,
            n_managers=parsed_n_managers,
            max_visits_per_day=max_visits_per_day,
            n_working_days=n_working_days,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Не удалось построить план: {error}") from error

    LAST_RESULT = result
    return result


@app.get("/api/export/routes.csv")
def export_routes() -> StreamingResponse:
    if LAST_RESULT is None:
        raise HTTPException(status_code=404, detail="Сначала постройте план")

    buffer = StringIO()
    pd.DataFrame(LAST_RESULT["routes"]).to_csv(buffer, index=False)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=routes.csv"},
    )
