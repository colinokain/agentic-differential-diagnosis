from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pathlib import Path
import json
from pydantic import BaseModel

from httpx import request
from agent import DiagnosticAgent, PatientSummarizationAgent, PatientTimelineSummarizationAgent, run_agent, stream_agent
from tables import get_available_tables, filter_table, get_table
from typing import Any

api = FastAPI(title="Diagnostic Pipeline")


class DiagnosisRequest(BaseModel):
    patient_id: int
    admission_id: int | None = None
    presenting_complaint: str | None = None
    patient_summary: str | None = None
    timeline_summary: str | None = None


def stream_agent_events(agent, patient_id: int, admission_id: int | None = None, presenting_complaint: str | None = None, extra_context: str | None = None):
    try:
        for event in stream_agent(agent, patient_id, admission_id, presenting_complaint, extra_context=extra_context):
            yield f"data: {json.dumps(event)}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

@api.get("/", response_class=HTMLResponse)
def frontend() -> HTMLResponse:
	return HTMLResponse(Path(__file__).with_name("index.html").read_text(encoding="utf-8"))

@api.get("/main.js")
def frontend_script() -> FileResponse:
	return FileResponse(Path(__file__).with_name("main.js"), media_type="text/javascript")

@api.get("/patient_ids")
def get_patient_ids() -> list[int]:
    return get_table("patients")["subject_id"].unique().tolist()

@api.get("/patient_stay_ids/{patient_id}")
def get_patient_stays(patient_id: int) -> list[int]:
    stays = filter_table("admissions", [{"column_name": "subject_id", "value": patient_id}])
    return stays["hadm_id"].unique().tolist()

@api.get("/patient_summary")
def get_patient_summary(patient_id: int, admission_id: int | None = None, presenting_complaint: str | None = None):
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    summarization_agent = PatientSummarizationAgent()

    admission_id = admission_id if admission_id != -1 else None

    summary = run_agent(summarization_agent, patient_id, admission_id, presenting_complaint)[-1]["content"]

    return summary


@api.get("/patient_summary/stream")
def stream_patient_summary(patient_id: int, admission_id: int | None = None, presenting_complaint: str | None = None):
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    summarization_agent = PatientSummarizationAgent()
    admission_id = admission_id if admission_id != -1 else None

    return StreamingResponse(
        stream_agent_events(summarization_agent, patient_id, admission_id, presenting_complaint),
        media_type="text/event-stream",
    )

@api.get("/patient_timeline_summary")
def get_patient_timeline_summary(patient_id: int, admission_id: int | None = None):
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    summarization_agent = PatientTimelineSummarizationAgent()

    admission_id = admission_id if admission_id != -1 else None

    timeline_summary = run_agent(summarization_agent, patient_id, admission_id)[-1]["content"]

    return timeline_summary


@api.get("/patient_timeline_summary/stream")
def stream_patient_timeline_summary(patient_id: int, admission_id: int | None = None, presenting_complaint: str | None = None):
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    summarization_agent = PatientTimelineSummarizationAgent()
    admission_id = admission_id if admission_id != -1 else None

    return StreamingResponse(
        stream_agent_events(summarization_agent, patient_id, admission_id, presenting_complaint),
        media_type="text/event-stream",
    )

@api.post("/diagnose")
def get_differential_diagnosis(payload: DiagnosisRequest):
    if not payload.patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    diagnosis_agent = DiagnosticAgent()

    admission_id = payload.admission_id if payload.admission_id != -1 else None
    extra_context = ""
    if payload.patient_summary:
        extra_context += f"Patient Summary:\n{payload.patient_summary}\n\n"
    if payload.timeline_summary:
        extra_context += f"Timeline Summary:\n{payload.timeline_summary}"

    diagnosis_result = run_agent(diagnosis_agent, payload.patient_id, admission_id, payload.presenting_complaint, extra_context=extra_context or None)[-1]

    return {
        "content": diagnosis_result["content"],
        "source_links": diagnosis_result.get("source_links", []),
    }


@api.post("/diagnose/stream")
def stream_differential_diagnosis(payload: DiagnosisRequest):
    if not payload.patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    diagnosis_agent = DiagnosticAgent()
    admission_id = payload.admission_id if payload.admission_id != -1 else None
    extra_context = ""
    if payload.patient_summary:
        extra_context += f"Patient Summary:\n{payload.patient_summary}\n\n"
    if payload.timeline_summary:
        extra_context += f"Timeline Summary:\n{payload.timeline_summary}"

    return StreamingResponse(
        stream_agent_events(diagnosis_agent, payload.patient_id, admission_id, payload.presenting_complaint, extra_context or None),
        media_type="text/event-stream",
    )