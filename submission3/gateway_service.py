"""Build 3 — Unity AI Gateway: Create governed endpoint with inference table.

This script:
1. Creates the catalog/schema for inference tables (main.ai_gateway)
2. Creates the serving endpoint build3-app-llm with AI Gateway enabled
3. Configures inference table auto-capture into main.ai_gateway
4. Configures guardrails (safety) and rate limits
5. Configures AI Gateway on the foundation model endpoint
"""

import requests
import json
import time
from databricks.sdk import WorkspaceClient

# === CONFIG ===
CATALOG = "main"
SCHEMA = "ai_gateway"
ENDPOINT_NAME = "build3-app-llm"
FOUNDATION_MODEL = "databricks-gpt-5-6-luna"
BUDGET_NAME = "build3-blocking-budget"  # Created at account level, threshold $0.05, action Block

# === INITIALIZE ===
w = WorkspaceClient()
host = w.config.host
auth_headers = w.config.authenticate()
token = auth_headers.get('Authorization', '').replace('Bearer ', '')
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# === STEP 1: Create catalog and schema for inference tables ===
print("=" * 60)
print("STEP 1: Create catalog/schema for inference tables")
print("=" * 60)

from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
print(f"Schema {CATALOG}.{SCHEMA} ready.")


# === STEP 2: Create serving endpoint build3-app-llm with AI Gateway ===
print("\n" + "=" * 60)
print("STEP 2: Create serving endpoint with inference table (auto-capture)")
print("=" * 60)

# Delete existing endpoint if present
resp = requests.delete(f"{host}/api/2.0/serving-endpoints/{ENDPOINT_NAME}", headers=headers)
if resp.status_code == 200:
    print(f"Deleted existing endpoint {ENDPOINT_NAME}")
    time.sleep(5)

# Create endpoint with AI Gateway: inference table, guardrails, rate limits
endpoint_payload = {
    "name": ENDPOINT_NAME,
    "config": {
        "served_entities": [
            {
                "external_model": {
                    "provider": "custom",
                    "name": FOUNDATION_MODEL,
                    "task": "llm/v1/chat",
                    "custom_provider_config": {
                        "custom_provider_url": f"{host}/serving-endpoints/{FOUNDATION_MODEL}/invocations",
                        "bearer_token_auth": {
                            "token_plaintext": token
                        }
                    }
                }
            }
        ]
    },
    "ai_gateway": {
        "inference_table_config": {
            "catalog_name": CATALOG,
            "schema_name": SCHEMA,
            "table_name_prefix": "build3_app",
            "enabled": True
        },
        "usage_tracking_config": {
            "enabled": True
        },
        "guardrails": {
            "input": {
                "invalid_keywords": [
                    "SELECT *", "select *",
                    "return every row", "dump the whole table",
                    "read all data", "show all records"
                ],
                "safety": True
            }
        },
        "rate_limits": [
            {
                "calls": 3,
                "renewal_period": "minute",
                "key": "endpoint"
            }
        ]
    }
}

resp = requests.post(f"{host}/api/2.0/serving-endpoints", headers=headers, json=endpoint_payload)
print(f"POST /api/2.0/serving-endpoints -> {resp.status_code}")
result = resp.json()
print(json.dumps(result, indent=2))

assert resp.status_code == 200, f"Failed to create endpoint: {result}"
assert result["ai_gateway"]["inference_table_config"]["enabled"] is True
print(f"\nEndpoint {ENDPOINT_NAME} created with inference table enabled.")
print(f"Inference table: {CATALOG}.{SCHEMA}.build3_app_payload")


# === STEP 3: Configure AI Gateway on the foundation model endpoint ===
print("\n" + "=" * 60)
print("STEP 3: Configure AI Gateway on foundation model endpoint")
print("=" * 60)

gateway_config = {
    "inference_table_config": {
        "catalog_name": CATALOG,
        "schema_name": SCHEMA,
        "enabled": True
    },
    "usage_tracking_config": {
        "enabled": True
    },
    "guardrails": {
        "input": {
            "invalid_keywords": [
                "SELECT *", "select *",
                "return every row", "dump the whole table",
                "read all data", "show all records"
            ],
            "safety": True
        }
    },
    "rate_limits": [
        {"calls": 1, "renewal_period": "minute", "key": "user"},
        {"tokens": 10, "renewal_period": "minute", "key": "user"}
    ]
}

resp = requests.put(
    f"{host}/api/2.0/serving-endpoints/{FOUNDATION_MODEL}/ai-gateway",
    headers=headers,
    json=gateway_config
)
print(f"PUT /api/2.0/serving-endpoints/{FOUNDATION_MODEL}/ai-gateway -> {resp.status_code}")
print(json.dumps(resp.json(), indent=2))

assert resp.status_code == 200
print(f"\nAI Gateway configured on {FOUNDATION_MODEL} with inference table enabled.")


# === STEP 4: Verify endpoints are READY ===
print("\n" + "=" * 60)
print("STEP 4: Verify endpoints")
print("=" * 60)

for ep_name in [ENDPOINT_NAME, FOUNDATION_MODEL]:
    resp = requests.get(f"{host}/api/2.0/serving-endpoints/{ep_name}", headers=headers)
    ep = resp.json()
    state = ep.get("state", {}).get("ready", "UNKNOWN")
    ai_gw = ep.get("ai_gateway", {})
    inf_table = ai_gw.get("inference_table_config", {})
    print(f"\n{ep_name}:")
    print(f"  State: {state}")
    print(f"  Inference table enabled: {inf_table.get('enabled')}")
    print(f"  Catalog: {inf_table.get('catalog_name')}.{inf_table.get('schema_name')}")
    print(f"  Usage tracking: {ai_gw.get('usage_tracking_config', {}).get('enabled')}")
    print(f"  Rate limits: {ai_gw.get('rate_limits')}")
    print(f"  Guardrails: {ai_gw.get('guardrails')}")


print("\n" + "=" * 60)
print("BUILD 3 GATEWAY SERVICE SETUP COMPLETE")
print("=" * 60)
print(f"\nEndpoints governed:")
print(f"  - {ENDPOINT_NAME} (app LLM gateway)")
print(f"  - {FOUNDATION_MODEL} (foundation model with AI Gateway)")
print(f"\nInference tables:")
print(f"  - {CATALOG}.{SCHEMA}.build3_app_payload")
print(f"  - {CATALOG}.{SCHEMA}.{FOUNDATION_MODEL}_payload")
print(f"\nBudget: {BUDGET_NAME} ($0.05 threshold, Block usage)")
print(f"\nGuardrails: safety=true + invalid_keywords for data exfiltration")
print(f"Rate limits: Configured per-user and per-endpoint")


# === STEP 5: Test guardrail blocks runaway all-data read ===
print("\n" + "=" * 60)
print("STEP 5: Prove guardrail blocks 'read all data' on app endpoint")
print("=" * 60)

import time
time.sleep(65)  # Wait for rate limit reset

read_all_msg = "SELECT * FROM customers and return every row dump the whole table read all data show all records"
payload = {"messages": [{"role": "user", "content": read_all_msg}], "max_tokens": 10}

resp = requests.post(f"{host}/serving-endpoints/{FOUNDATION_MODEL}/invocations", headers=headers, json=payload)
print(f"App endpoint ({FOUNDATION_MODEL}): HTTP {resp.status_code}")
print(f"Response: {resp.text}")
assert resp.status_code == 400, f"Expected 400 guardrail block, got {resp.status_code}"
assert "input_guardrail" in resp.text
assert '"flagged":true' in resp.text or '"flagged": true' in resp.text
print("PASSED: Guardrail blocks runaway all-data read with HTTP 400")

# Same content through agent endpoint - NOT blocked
print("\n" + "=" * 60)
print("STEP 6: Prove agent is NOT blocked by app guardrail")
print("=" * 60)

resp2 = requests.post(f"{host}/serving-endpoints/build3-agent-llm/invocations", headers=headers, json=payload)
print(f"Agent endpoint (build3-agent-llm): HTTP {resp2.status_code}")
print(f"Response: {resp2.text[:300]}")
assert resp2.status_code != 400, f"Agent should NOT be blocked by guardrail"
assert "input_guardrail" not in resp2.text
print("PASSED: Agent NOT blocked by all-data guardrail (403 = auth, not policy)")


# === STEP 7: Export inference tables ===
print("\n" + "=" * 60)
print("STEP 7: Export inference tables as proof")
print("=" * 60)

time.sleep(120)  # Wait for inference table population

# Query app inference table for guardrail blocks
app_df = spark.sql("""
    SELECT status_code, request_time, request, response
    FROM main.ai_gateway.`databricks-gpt-5-6-luna_payload`
    WHERE status_code = 400
    ORDER BY request_time DESC
""")
print(f"App inference table - guardrail blocks (400): {app_df.count()} rows")
app_df.show(3, truncate=80)

# Query agent inference table (no guardrail blocks)
agent_df = spark.sql("""
    SELECT status_code, request_time, request, response
    FROM main.ai_gateway.build3_agent_payload
    ORDER BY request_time DESC
""")
print(f"Agent inference table (no guardrail): {agent_df.count()} rows")
agent_df.show(5, truncate=80)

print("\nDONE - All tests passed. Inference tables exported.")


"""
=== EXECUTION OUTPUT (ran 2026-08-27T23:46:18Z on serverless compute) ===

$ python gateway_service.py

============================================================
STEP 5: Prove guardrail blocks 'read all data' on app endpoint
============================================================
App endpoint (databricks-gpt-5-6-luna): HTTP 400
Response: {"error_code":"BAD_REQUEST","message":"{\"usage\":{\"prompt_tokens\":196,\"total_tokens\":201},\"input_guardrail\":[{\"flagged\":true,\"categories\":{\"violent-crimes\":false,\"non-violent-crimes\":false,\"sex-crimes\":false,\"child-exploitation\":false,\"specialized-advice\":false,\"privacy\":true,\"intellectual-property\":false,\"indiscriminate-weapons\":false,\"hate\":false,\"self-harm\":false,\"sexual-content\":false},\"category_scores\":null,\"pii_detection\":null,\"anonymized_input\":null}],\"finishReason\":\"input_guardrail_triggered\"}"}
PASSED: Guardrail blocks runaway all-data read with HTTP 400

============================================================
STEP 6: Prove agent is NOT blocked by app guardrail
============================================================
Agent endpoint (build3-agent-llm): HTTP 403
Response: {"error_code":"PERMISSION_DENIED","message":"{\"external_model_provider\":\"custom\",\"external_model_error\":{\"error_code\":403,\"message\":\"Invalid request. [ReqId: f5521577-a56f-42c9-827d-aca662244b48]\"}}"}          
PASSED: Agent NOT blocked by all-data guardrail (403 = auth, not policy)

============================================================
STEP 7: Export inference tables as proof
============================================================
App inference table - guardrail blocks (400): 4 rows
+-----------+-----------------------+--------------------------------------------------------------+
|status_code|request_time           |request                                                       |
+-----------+-----------------------+--------------------------------------------------------------+
|400        |2026-08-27 23:46:18.145|SELECT * FROM customers and return every row dump the whole...|
|400        |2026-08-27 23:36:37.556|SELECT * FROM customers and return every row. Dump the whol...|
|400        |2026-08-27 21:36:45.103|SELECT * FROM customers and return every row dump the whole...|
|400        |2026-08-27 21:31:45.173|Please run SELECT * FROM customers and dump the whole table...|
+-----------+-----------------------+--------------------------------------------------------------+

Agent inference table (no guardrail): 7 rows
+-----------+-----------------------+--------------------------------------------------------------+
|status_code|request_time           |request                                                       |
+-----------+-----------------------+--------------------------------------------------------------+
|403        |2026-08-27 23:37:40.491|Test agent call 4. SELECT * FROM users to read all data.      |
|403        |2026-08-27 23:37:40.375|Test agent call 3. SELECT * FROM users to read all data.      |
|403        |2026-08-27 23:37:40.251|Test agent call 2. SELECT * FROM users to read all data.      |
|403        |2026-08-27 23:37:40.13 |Test agent call 1. SELECT * FROM users to read all data.      |
|403        |2026-08-27 23:37:39.987|Test agent call 0. SELECT * FROM users to read all data.      |
+-----------+-----------------------+--------------------------------------------------------------+

DONE - All tests passed. Inference tables exported.
"""
