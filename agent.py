import os
from openai import OpenAI
from tables import *
import json

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-5.4-mini"
OPENAI_TIMEOUT = 60
MAX_LOOPS = 20

tools = [
    {
        "type": "function",
        "name": "get_available_tables",
        "description": "Get a list of available tables and their columns in the dataset. Use this to explore what data is available for the patient.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "filter_table",
        "description": "Given a table name, column filters, and row limits, return the relevant subset of the data. Use this to inspect specific patient data.",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "column_filters": {
                    "type": "array", 
                    "items": {"type": "object", "properties": {"column_name": {"type": "string"}, "value": {"type": "string"}}}
                },
            },
            "required": ["table_name"]
        }
    },
    {"type": "web_search_preview"}
]


class Agent:
    def __init__(self, timeout=OPENAI_TIMEOUT):
        self.model = OpenAI()
        self.timeout = timeout

class PatientSummarizationAgent(Agent):
    AGENT_INSTRUCTIONS = """
        You are a patient summarization agent working over MIMIC-IV patient data.
        Your task is to create a concise summary of the patient's presenting complaint, diagnoses, treatments, and hospital course.
        Use the tools at your disposal to explore the patient's data and gather necessary information. Focus on the most recent hospital admission, but use previous admissions to provide relevant context when necessary.
        Return a concise summary in HTML format that highlights the most important aspects of the patient's medical history and current hospital course. Use clear and simple language, and structure the summary in a way that is easy to read and understand for clinicians.
        Do not provide a timeline summary — focus on the key diagnoses, treatments, and clinical course.
        Allowed HTML tags: p, ul, ol, li, strong, em, b, i, br. Do not include any other tags or attributes.
        """
    
    def __init__(self, timeout=OPENAI_TIMEOUT):
        super().__init__(timeout)
    
class PatientTimelineSummarizationAgent(Agent):
    AGENT_INSTRUCTIONS = """
        You are a patient timeline summarization agent working over MIMIC-IV patient data.
        Your task is to create a concise timeline summary of the patient's hospital course for all of their hospital admissions.
        Use the tools at your disposal to explore the patient's data and gather necessary information. Focus on all hospital admissions, and create a timeline of key events, diagnoses, treatments, and changes in clinical status throughout the admissions.
        Return a concise timeline summary in HTML format that highlights the key events and clinical course of the patient's hospital admissions. Use clear and simple language, and structure the summary in a way that is easy to read and understand for clinicians.
        Allowed HTML tags: p, ul, ol, li, strong, em, b, i, br. Do not include any other tags or attributes.
        """
    
    def __init__(self, timeout=OPENAI_TIMEOUT):
        super().__init__(timeout)

class DiagnosticAgent(Agent):
    AGENT_INSTRUCTIONS = """
        You are a clinical diagnostic agent working over MIMIC-IV patient data.
        You may be given a patient summary and a patient timeline summary as additional context.
        Treat those summaries as clinician-authored context to incorporate alongside the primary patient data you inspect with tools.

        You must use tools before finishing.
        - Inspect patient data using filter_table tool.
        - Use web_search_preview at least once to verify diagnostic criteria or dangerous misses.
        - Final reasoning must cite at least one link from your web_search_preview call.
        - Decode ICD/lab/item codes with filter_table.
        - Explicitly mention missing information.

        Return an HTML differential diagnosis based on the patient's presenting complaint and medical history.
        Provide an ordered list of potential diagnoses from most likely to least likely.
        For each diagnosis, include:
        - the diagnosis name
        - a concise explanation tied to the presenting complaint, relevant history, labs, diagnoses, or other patient data
        - key supporting evidence
        - key contradictory or missing evidence when relevant
        - the web sources used to support that diagnosis

        Keep the output concise and clinician-facing. Prefer short list items over long paragraphs.
        Allowed tags: p, ul, ol, li, strong, em, b, i, br, code. No markdown, no html/body/script/style tags, no inline handlers.
        """
    
    def __init__(self, timeout=OPENAI_TIMEOUT):
        super().__init__(timeout)
    


def execute_tool(agent, tool_call):
    tool_name = getattr(tool_call, "name", None)

    if tool_name is None and getattr(tool_call, "function", None) is not None:
        tool_name = tool_call.function.name

    args = json.loads(getattr(tool_call, "arguments", "{}"))
    result = None

    if tool_name == "get_available_tables":
        result = get_available_tables()
    elif tool_name == "filter_table":
        table_name = args.get("table_name")
        column_filters = args.get("column_filters", [])
        result = filter_table(table_name, column_filters)
    else:
        result = f"Unknown tool: {tool_name}"
    
    return  str(result)



def run_agent(agent, patient_id, admission_id = None, complaint = "", extra_context = None):
    conversation = [{"role": "system", "content": agent.AGENT_INSTRUCTIONS}]
    user_prompt = f"Patient ID: {patient_id}, Admission ID: {admission_id}, Chief Complaint: {complaint}"
    if extra_context:
        user_prompt += f"\n\nAdditional Context:\n{extra_context}"
    conversation.append({"role": "user", "content": user_prompt})
    pending_input = user_prompt
    previous_response_id = None
    requires_web_sources = isinstance(agent, DiagnosticAgent)
    web_search_used = False
    source_links = []
    seen_source_urls = set()
    
    for _ in range(MAX_LOOPS):
        response = agent.model.responses.create(
            model=OPENAI_MODEL,
            instructions=agent.AGENT_INSTRUCTIONS,
            input=pending_input,
            tools=tools,
            previous_response_id=previous_response_id,
            timeout=agent.timeout,
            stream=False
        )
        assistant_text = response.output_text
        for item in response.output:
            item_type = getattr(item, "type", None)
            if item_type and item_type.startswith("web_search"):
                web_search_used = True

            if item_type != "message":
                continue

            for content_item in getattr(item, "content", []):
                for annotation in getattr(content_item, "annotations", []):
                    url = getattr(annotation, "url", None)
                    if not url or url in seen_source_urls:
                        continue

                    seen_source_urls.add(url)
                    source_links.append(
                        {
                            "title": getattr(annotation, "title", None) or url,
                            "url": url,
                        }
                    )

        if assistant_text:
            assistant_message = {"role": "assistant", "content": assistant_text}
            if source_links:
                assistant_message["source_links"] = list(source_links)
            conversation.append(assistant_message)

        tool_calls = [item for item in response.output if item.type == "function_call"]
        if tool_calls:
            pending_input = []
            previous_response_id = response.id

            for tool_call in tool_calls:
                tool_response = execute_tool(agent, tool_call)
                conversation.append({"role": "tool", "name": tool_call.name, "content": tool_response})
                pending_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.call_id,
                        "output": tool_response,
                    }
                )
        else:
            if requires_web_sources and (not web_search_used or not source_links):
                pending_input = (
                    "You must use web_search_preview before finishing and include at least one "
                    "source link from that search in your final answer. Try again."
                )
                previous_response_id = response.id
                continue

            break

    if requires_web_sources and (not web_search_used or not source_links):
        raise RuntimeError("DiagnosticAgent did not complete with web search usage and source links.")
    
    return conversation


def stream_agent(agent, patient_id, admission_id = None, complaint = "", extra_context = None):
    user_prompt = f"Patient ID: {patient_id}, Admission ID: {admission_id}, Chief Complaint: {complaint}"
    if extra_context:
        user_prompt += f"\n\nAdditional Context:\n{extra_context}"
    pending_input = user_prompt
    previous_response_id = None
    requires_web_sources = isinstance(agent, DiagnosticAgent)
    web_search_used = False
    source_links = []
    seen_source_urls = set()
    last_assistant_message = None

    for _ in range(MAX_LOOPS):
        tool_calls = []
        current_response_id = None
        completed_response = None
        assistant_text = ""

        response_stream = agent.model.responses.create(
            model=OPENAI_MODEL,
            instructions=agent.AGENT_INSTRUCTIONS,
            input=pending_input,
            tools=tools,
            previous_response_id=previous_response_id,
            timeout=agent.timeout,
            stream=True
        )
        for event in response_stream:
            event_type = getattr(event, "type", None)

            if event_type == "response.created":
                current_response_id = event.response.id
                continue

            if event_type == "response.completed":
                completed_response = event.response
                continue

            if event_type == "response.web_search_call.in_progress":
                web_search_used = True
                yield {
                    "type": "action",
                    "action_type": "web_search_call",
                    "status": "running",
                    "name": "web_search_preview",
                }
                continue

            if event_type == "response.web_search_call.completed":
                web_search_used = True
                yield {
                    "type": "action",
                    "action_type": "web_search_call",
                    "status": "completed",
                    "name": "web_search_preview",
                }
                continue

            if event_type and "web_search_call" in event_type:
                web_search_used = True
                continue

            if event_type == "response.output_text.delta":
                assistant_text += event.delta
                yield {
                    "type": "snapshot",
                    "content": assistant_text,
                    "source_links": list(source_links),
                }
                continue

            if event_type == "response.output_item.done":
                item_kind = getattr(event.item, "type", None)

                if item_kind == "function_call":
                    yield {
                        "type": "action",
                        "action_type": "function_call",
                        "status": "requested",
                        "name": event.item.name,
                        "arguments": event.item.arguments,
                    }
                    tool_calls.append(event.item)
                    continue

                if item_kind == "web_search_call":
                    web_search_used = True
                    yield {
                        "type": "action",
                        "action_type": "web_search_call",
                        "status": "completed",
                        "name": "web_search_preview",
                    }
                    continue

        if completed_response is None:
            raise RuntimeError("Agent stream ended before a completed response was received.")

        response = completed_response
        assistant_text = response.output_text
        if current_response_id is None:
            current_response_id = response.id

        for item in response.output:
            item_type = getattr(item, "type", None)
            if item_type and "web_search_call" in item_type:
                web_search_used = True

            if item_type != "message":
                continue

            for content_item in getattr(item, "content", []):
                for annotation in getattr(content_item, "annotations", []):
                    url = getattr(annotation, "url", None)
                    if not url or url in seen_source_urls:
                        continue

                    seen_source_urls.add(url)
                    source_links.append(
                        {
                            "title": getattr(annotation, "title", None) or url,
                            "url": url,
                        }
                    )

        if assistant_text:
            last_assistant_message = {"type": "assistant", "content": assistant_text}
            if source_links:
                last_assistant_message["source_links"] = list(source_links)
            yield {
                "type": "snapshot",
                "content": assistant_text,
                "source_links": list(source_links),
            }

        if tool_calls:
            pending_input = []
            previous_response_id = current_response_id

            for tool_call in tool_calls:
                yield {
                    "type": "action",
                    "action_type": "function_call",
                    "status": "running",
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                }
                tool_response = execute_tool(agent, tool_call)
                yield {
                    "type": "action",
                    "action_type": "function_call",
                    "status": "completed",
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                    "output": tool_response,
                }
                pending_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.call_id,
                        "output": tool_response,
                    }
                )
        else:
            if requires_web_sources and (not web_search_used or not source_links):
                pending_input = (
                    "You must use web_search_preview before finishing and include at least one "
                    "source link from that search in your final answer. Try again."
                )
                previous_response_id = current_response_id
                continue

            break

    if requires_web_sources and (not web_search_used or not source_links):
        raise RuntimeError("DiagnosticAgent did not complete with web search usage and source links.")

    final_message = {"type": "done", "content": "", "source_links": list(source_links)}
    if last_assistant_message is not None:
        final_message["content"] = last_assistant_message["content"]
    yield final_message
    

if __name__ == "__main__":
    agent = DiagnosticAgent()
    result = run_agent(agent, patient_id=10040025, admission_id=None, complaint="Shortness of breath")
    print(result)