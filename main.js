"use strict";
const activeAgentStreams = {};
const agentStreamLabels = {
    summary: "Summary",
    timeline: "Timeline",
    diagnosis: "Diagnosis"
};

$(document).ready(function() 
{
    // Fetch patient IDs and populate the dropdown
    $.ajax({
        url: "/patient_ids",
        method: "GET",
        success: function(data)
        {
            populatePatientDropdown(data);
        },
        error: function(error)
        {
            console.error("Error fetching patient IDs:", error);
        }
    });

    $("#patient_select").change(function()
    {
        const selectedPatient = $(this).val();
        $.ajax({
            url: `/patient_stay_ids/${selectedPatient}`,
            method: "GET",
            success: function(data)
            {
                populateStayDropdown(data);
            },
            error: function(error)
            {
                console.error("Error fetching patient data:", error);
            }
        });
    });

    $("#run_pipeline_button").click(function()
    {
        const selectedPatient = $("#patient_select").val();
        const selectedStay = $("#stay_select").val();
        const presentingComplaint = $("#presenting_complaint").val();
        const requestParams = {
            patient_id: selectedPatient,
            admission_id: selectedStay,
            presenting_complaint: presentingComplaint
        };

        closeAllAgentStreams();
        clearAgentActions();
        populatePatientSummary({ content: "<p>Loading...</p>" });
        populatePatientTimelineSummary({ content: "<p>Loading...</p>" });
        populateDifferentialDiagnosis({ content: "<p>Waiting for patient summary and timeline...</p>", source_links: [] });

        const summaryPromise = startAgentStream("summary", "/patient_summary/stream", requestParams, populatePatientSummary);
        const timelinePromise = startAgentStream("timeline", "/patient_timeline_summary/stream", requestParams, populatePatientTimelineSummary);

        Promise.all([summaryPromise, timelinePromise]).then(function(results)
        {
            const diagnosisParams = {
                patient_id: selectedPatient,
                admission_id: selectedStay,
                presenting_complaint: presentingComplaint,
                patient_summary: results[0].content || "",
                timeline_summary: results[1].content || ""
            };

            populateDifferentialDiagnosis({ content: "<p>Loading...</p>", source_links: [] });
            startAgentPostStream("diagnosis", "/diagnose/stream", diagnosisParams, populateDifferentialDiagnosis);
        }).catch(function(error)
        {
            console.error("Error preparing diagnosis context:", error);
            populateDifferentialDiagnosis({ content: `<p>${error.message || error}</p>`, source_links: [] });
        });
    });

});

function buildStreamUrl(path, params)
{
    const queryParams = new URLSearchParams();

    Object.entries(params).forEach(function([key, value])
    {
        if (value !== undefined && value !== null)
        {
            queryParams.append(key, value);
        }
    });

    return `${path}?${queryParams.toString()}`;
}

function closeAgentStream(streamKey)
{
    if (!activeAgentStreams[streamKey])
    {
        return;
    }

    activeAgentStreams[streamKey].close();
    delete activeAgentStreams[streamKey];
}

function closeAllAgentStreams()
{
    Object.keys(activeAgentStreams).forEach(function(streamKey)
    {
        closeAgentStream(streamKey);
    });
}

function startAgentStream(streamKey, path, params, onUpdate)
{
    return new Promise(function(resolve, reject)
    {
        const streamUrl = buildStreamUrl(path, params);
        const eventSource = new EventSource(streamUrl);
        let finalPayload = null;

        activeAgentStreams[streamKey] = eventSource;

        eventSource.onmessage = function(event)
        {
            const payload = JSON.parse(event.data);

            if (payload.type === "error")
            {
                console.error(`Error streaming ${streamKey}:`, payload.error);
                appendAgentAction(streamKey, {
                    action_type: "stream",
                    status: "error",
                    name: payload.error
                });
                onUpdate({ content: `<p>${payload.error}</p>`, source_links: [] });
                closeAgentStream(streamKey);
                reject(new Error(payload.error));
                return;
            }

            if (payload.type === "action")
            {
                appendAgentAction(streamKey, payload);
                return;
            }

            onUpdate(payload);
            finalPayload = payload;

            if (payload.type === "done")
            {
                closeAgentStream(streamKey);
                resolve(finalPayload || payload);
            }
        };

        eventSource.onerror = function(error)
        {
            console.error(`Stream connection error for ${streamKey}:`, error);
            closeAgentStream(streamKey);
            reject(new Error(`Stream connection error for ${streamKey}`));
        };
    });
}

function populatePatientDropdown(patientIds)
{
    const dropdown = $("#patient_select");
    patientIds.forEach(id => 
    {
        dropdown.append(new Option(id, id));
    });
}

function populateStayDropdown(stayIds)
{
    const dropdown = $("#stay_select");
    dropdown.empty();
    dropdown.append(new Option("All", -1));
    stayIds.forEach(id => 
    {
        dropdown.append(new Option(id, id));
    });
}

function populatePatientSummary(summary)
{
    const summaryDiv = $("#patient_summary");
    summaryDiv.empty();
    summaryDiv.append("<h3>Patient Summary</h3>");
    summaryDiv.append(typeof summary === "string" ? summary : (summary.content || ""));
    
}

function populatePatientTimelineSummary(timelineSummary)
{
    const timelineDiv = $("#patient_timeline_summary");
    timelineDiv.empty();
    timelineDiv.append("<h3>Patient Timeline Summary</h3>");
    timelineDiv.append(typeof timelineSummary === "string" ? timelineSummary : (timelineSummary.content || ""));
}

function populateDifferentialDiagnosis(diagnosisData)
{
    const diagnosisDiv = $("#patient_diagnoses");
    diagnosisDiv.empty();
    diagnosisDiv.append(`<h3>Differential Diagnosis</h3>`);

    diagnosisDiv.append(diagnosisData.content || "");

    if (!diagnosisData.source_links || diagnosisData.source_links.length === 0)
    {
        return;
    }

    diagnosisDiv.append("<h4>Sources</h4>");
    const sourceList = $("<ul></ul>");

    diagnosisData.source_links.forEach(function(sourceLink)
    {
        const listItem = $("<li></li>");
        const link = $("<a></a>")
            .attr("href", sourceLink.url)
            .attr("target", "_blank")
            .attr("rel", "noopener noreferrer")
            .text(sourceLink.title || sourceLink.url);

        listItem.append(link);
        sourceList.append(listItem);
    });

    diagnosisDiv.append(sourceList);
}

function clearAgentActions()
{
    $("#agent_actions_body").empty();
}

function appendAgentAction(streamKey, action)
{
    const actionsBody = $("#agent_actions_body");
    const actionName = formatAgentActionName(action);
    const statusText = formatAgentActionStatus(action.status);
    const details = [];

    if (action.arguments)
    {
        details.push(`<div><small><strong>Args:</strong> ${escapeHtml(action.arguments)}</small></div>`);
    }

    if (action.output)
    {
        details.push(`<div><small><strong>Output:</strong> ${escapeHtml(truncateText(action.output, 240))}</small></div>`);
    }

    const row = $(
        `<tr>
            <td>${agentStreamLabels[streamKey] || streamKey}</td>
            <td><div>${actionName}</div>${details.join("")}</td>
            <td>${statusText}</td>
        </tr>`
    );

    actionsBody.append(row);
    const actionsContainer = $("#agent_actions");
    actionsContainer.scrollTop(actionsContainer[0].scrollHeight);
}

function formatAgentActionName(action)
{
    if (action.action_type === "web_search_call")
    {
        return "web_search_preview";
    }

    return action.name || action.action_type || "action";
}

function formatAgentActionStatus(status)
{
    if (!status)
    {
        return "";
    }

    return status.charAt(0).toUpperCase() + status.slice(1);
}

function truncateText(value, maxLength)
{
    if (!value || value.length <= maxLength)
    {
        return value || "";
    }

    return `${value.slice(0, maxLength)}...`;
}

function escapeHtml(value)
{
    return $("<div></div>").text(value || "").html();
}

function startAgentPostStream(streamKey, path, payload, onUpdate)
{
    return new Promise(function(resolve, reject)
    {
        const abortController = new AbortController();
        activeAgentStreams[streamKey] = {
            close: function()
            {
                abortController.abort();
            }
        };

        fetch(path, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload),
            signal: abortController.signal
        }).then(function(response)
        {
            if (!response.ok)
            {
                throw new Error(`HTTP ${response.status}`);
            }

            if (!response.body)
            {
                throw new Error("Streaming response body is missing");
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let finalPayload = null;

            function processBuffer()
            {
                const events = buffer.split("\n\n");
                buffer = events.pop() || "";

                events.forEach(function(rawEvent)
                {
                    const dataLine = rawEvent.split("\n").find(function(line)
                    {
                        return line.startsWith("data: ");
                    });

                    if (!dataLine)
                    {
                        return;
                    }

                    const payload = JSON.parse(dataLine.slice(6));

                    if (payload.type === "error")
                    {
                        console.error(`Error streaming ${streamKey}:`, payload.error);
                        appendAgentAction(streamKey, {
                            action_type: "stream",
                            status: "error",
                            name: payload.error
                        });
                        onUpdate({ content: `<p>${payload.error}</p>`, source_links: [] });
                        closeAgentStream(streamKey);
                        reject(new Error(payload.error));
                        return;
                    }

                    if (payload.type === "action")
                    {
                        appendAgentAction(streamKey, payload);
                        return;
                    }

                    onUpdate(payload);
                    finalPayload = payload;

                    if (payload.type === "done")
                    {
                        closeAgentStream(streamKey);
                        resolve(finalPayload || payload);
                    }
                });
            }

            function pump()
            {
                reader.read().then(function(result)
                {
                    if (result.done)
                    {
                        processBuffer();
                        return;
                    }

                    buffer += decoder.decode(result.value, { stream: true });
                    processBuffer();
                    pump();
                }).catch(function(error)
                {
                    closeAgentStream(streamKey);
                    reject(error);
                });
            }

            pump();
        }).catch(function(error)
        {
            if (abortController.signal.aborted)
            {
                return;
            }

            console.error(`Stream connection error for ${streamKey}:`, error);
            closeAgentStream(streamKey);
            reject(error);
        });
    });
}