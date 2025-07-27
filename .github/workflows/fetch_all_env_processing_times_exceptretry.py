import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
from urllib.parse import quote
import json
import os
import re

# ------------------------
# Load environment configs from environment variables (secrets)
# ------------------------
environments = {
    "DEV": {
        "SAP_USERNAME": os.getenv("DEV_SAP_USERNAME"),
        "SAP_PASSWORD": os.getenv("DEV_SAP_PASSWORD"),
        "SAP_BASE_URL": os.getenv("DEV_SAP_BASE_URL")
    },
    "UAT": {
        "SAP_USERNAME": os.getenv("UAT_SAP_USERNAME"),
        "SAP_PASSWORD": os.getenv("UAT_SAP_PASSWORD"),
        "SAP_BASE_URL": os.getenv("UAT_SAP_BASE_URL")
    },
    "PROD": {
        "SAP_USERNAME": os.getenv("PROD_SAP_USERNAME"),
        "SAP_PASSWORD": os.getenv("PROD_SAP_PASSWORD"),
        "SAP_BASE_URL": os.getenv("PROD_SAP_BASE_URL")
    }
}

# Unified CPI iFlow credentials and URL (from secrets)
IFLOW_URL = os.getenv("IFLOW_URL")
IFLOW_USERNAME = os.getenv("IFLOW_USERNAME")
IFLOW_PASSWORD = os.getenv("IFLOW_PASSWORD")

# ------------------------
# Validate environment variables
# ------------------------
required_secrets = [IFLOW_URL, IFLOW_USERNAME, IFLOW_PASSWORD]
for env_config in environments.values():
    required_secrets.extend(env_config.values())

if not all(required_secrets):
    raise RuntimeError(" One or more required environment variables are missing.")

# ------------------------
# Time range: past 24 hours in UTC
# ------------------------
end_time = datetime.utcnow()
start_time = end_time - timedelta(days=1)
start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
end_str = end_time.strftime("%Y-%m-%dT%H:%M:%S")

# Final payload holder
final_payload = {
    "timestamp_range": {
        "start": start_str,
        "end": end_str
    },
    "environments": {}
}

# ------------------------
# Execution per Environment
# ------------------------
for env, config in environments.items():
    print(f"\n\n Running for Environment: {env}\n{'-' * 40}")

    # Aligning filter with correct logic (LogEnd >= start AND LogStart <= end)
    filter_str = f"LogEnd ge datetime'{start_str}' and LogStart le datetime'{end_str}'"
    encoded_filter = quote(filter_str)
    initial_url = (
        f"{config['SAP_BASE_URL']}/MessageProcessingLogs"
        f"?$format=json&$select=IntegrationFlowName,LogStart,LogEnd,MessageGuid,Status&$filter={encoded_filter}"
    )

    all_results = []
    next_url = initial_url

    while next_url:
        print(f"Requesting: {next_url}")
        response = requests.get(next_url, auth=HTTPBasicAuth(config['SAP_USERNAME'], config['SAP_PASSWORD']))

        if response.status_code == 200:
            data = response.json()
            results = data.get("d", {}).get("results", [])
            all_results.extend(results)

            next_link = data.get("d", {}).get("__next")
            if next_link:
                next_url = next_link if next_link.startswith("http") else f"{config['SAP_BASE_URL'].rstrip('/')}/{next_link.lstrip('/')}"
            else:
                next_url = None
        else:
            print(f" Request failed for {env}. Status: {response.status_code}")
            print(response.text)
            break

    print(f" Collected {len(all_results)} records for {env}")

    # ------------------------
    # Duration Calculation
    # ------------------------
    def parse_log_date(date_str):
        match = re.search(r'/Date\((\d+)\)/', date_str)
        return int(match.group(1)) if match else None

    duration_records = []
    for entry in all_results:
        start_ms = parse_log_date(entry["LogStart"])
        end_ms = parse_log_date(entry["LogEnd"])
        if start_ms is None or end_ms is None:
            continue
        duration = end_ms - start_ms
        duration_records.append({
            "IntegrationFlowName": entry["IntegrationFlowName"],
            "MessageGuid": entry["MessageGuid"],
            "DurationMs": duration,
            "LogStart": start_ms,
            "LogEnd": end_ms,
            "Status": entry.get("Status")
        })

    # ------------------------
    # Max Duration per iFlow (excluding RETRYs)
    # ------------------------
    max_durations = {}
    for record in duration_records:
        if record["Status"] == "RETRY":
            continue  # Skip RETRY records

        name = record["IntegrationFlowName"]
        if name not in max_durations or record["DurationMs"] > max_durations[name]["DurationMs"]:
            max_durations[name] = record

    # ------------------------
    # Top 5 iFlows by max duration
    # ------------------------
    top_5 = sorted(max_durations.values(), key=lambda x: x["DurationMs"], reverse=True)[:5]

    print(f"\n Top 5 iFlows in {env}")
    for idx, entry in enumerate(top_5, 1):
        print(f"#{idx}: {entry['IntegrationFlowName']} - {entry['DurationMs']} ms")

    # Add to final payload
    final_payload["environments"][env] = {
        "Top5IflowsByDuration": top_5,
        "TotalMessagesProcessed": len(all_results)
    }

# ------------------------
# Send consolidated payload to CPI iFlow
# ------------------------
print(f"\n Sending consolidated payload to CPI iFlow: {IFLOW_URL}")
post_response = requests.post(
    url=IFLOW_URL,
    auth=HTTPBasicAuth(IFLOW_USERNAME, IFLOW_PASSWORD),
    headers={"Content-Type": "application/json"},
    data=json.dumps(final_payload)
)

if post_response.status_code in [200, 201, 202]:
    print(f" Sent successfully. Status Code: {post_response.status_code}")
else:
    print(f" Failed to send. Status Code: {post_response.status_code}")
    print(post_response.text)
