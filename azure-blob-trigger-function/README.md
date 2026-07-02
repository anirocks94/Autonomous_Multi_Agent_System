# Azure Blob Trigger Function — Exception Ingestion

## What This Does

This Azure Function **replaces the polling loop** in `MonitorAgent` with an **event-driven Blob Storage trigger**. Whenever a new `.csv` exception file is uploaded to the `exceptions` container, this function fires instantly and:

1. **Parses the CSV** — supports both App Insights export format and the simple `error_type,message,stack_trace,frequency` format
2. **Filters actionability** — checks the error type against the known .NET exception allowlist
3. **Builds a `DebugState` dict** — identical shape to what `MonitorAgent.detect_errors()` produces
4. **Publishes to Azure Service Bus** — enqueues the job so `main.py` / the dashboard can consume it without polling

## Architecture

```
Azure Blob Storage
  [exceptions/] ← new CSV uploaded
        │
        ▼
 Azure Function (Blob Trigger)
  ├── csv_parser.py          parse raw bytes → error_data dict
  ├── actionability_filter.py  allowlist gate
  ├── state_builder.py       build DebugState + publish to Service Bus
  └── [archives blob to processed/ or skipped/]
        │
        ▼
 Azure Service Bus Queue [debug-jobs]
        │
        ▼
 main.py / dashboard
  (reads from Service Bus instead of polling Blob Storage)
```

## Folder Structure

```
azure-blob-trigger-function/
├── function_app.py          ← Blob trigger entry point (Azure Functions v2)
├── csv_parser.py            ← CSV parsing (App Insights + simple format)
├── actionability_filter.py  ← .NET exception allowlist
├── state_builder.py         ← DebugState builder + Service Bus publisher
├── host.json                ← Functions host config
├── local.settings.json      ← Local dev env vars (DO NOT COMMIT)
├── requirements.txt         ← Python dependencies
└── README.md                ← This file
```

## Running Locally

### Prerequisites

```bash
# Install Azure Functions Core Tools v4
npm install -g azure-functions-core-tools@4 --unsafe-perm true

# Install Python deps
pip install -r requirements.txt
```

### Setup

1. Fill in `local.settings.json` (or copy from `.env`):
   ```json
   {
     "AZURE_BLOB_CONNECTION_STRING": "<your-storage-connection-string>",
     "SERVICE_BUS_CONNECTION_STRING": "<your-service-bus-connection-string>",
     "LOCAL_DEV": "true"
   }
   ```
   > `LOCAL_DEV=true` skips the real Service Bus call and prints the `DebugState` JSON to stdout.

2. Start the function:
   ```bash
   cd azure-blob-trigger-function
   func start
   ```

3. Upload a test CSV to trigger it:
   ```bash
   az storage blob upload \
     --connection-string "<conn>" \
     --container-name exceptions \
     --name test_error.csv \
     --file ../data/sample_exception.csv
   ```

4. Check the terminal — you should see:
   ```
   🔔 Blob trigger fired: test_error.csv
   ✅ Parsed error: type=System.NullReferenceException, count=5
   ✅ Actionable error type: System.NullReferenceException
   📨 DebugState (would be sent to Service Bus):
   { "session_id": "abc12345", "error_event": { ... }, ... }
   📦 Blob archived to processed/test_error.csv
   ```

## Deploying to Azure

```bash
# Login
az login

# Create function app (if not exists)
az functionapp create \
  --resource-group <rg> \
  --consumption-plan-location eastus \
  --runtime python \
  --runtime-version 3.11 \
  --functions-version 4 \
  --name <function-app-name> \
  --storage-account <storage-account-name>

# Deploy
func azure functionapp publish <function-app-name>
```

Set the env vars in the Function App **Application Settings** in the Azure portal (same keys as `local.settings.json`), and set `LOCAL_DEV=false`.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AzureWebJobsStorage` | ✅ | — | Storage account for Functions internal state |
| `AZURE_BLOB_CONNECTION_STRING` | ✅ | — | Storage account with the `exceptions` container |
| `AZURE_BLOB_CONTAINER` | ✅ | `exceptions` | Container name to watch |
| `SERVICE_BUS_CONNECTION_STRING` | ✅ | — | Service Bus namespace connection string |
| `SERVICE_BUS_QUEUE_NAME` | ✅ | `debug-jobs` | Queue to publish DebugState jobs to |
| `MAX_ATTEMPTS` | ❌ | `3` | Passed into initial DebugState |
| `MAX_REVIEW_POLLS` | ❌ | `20` | Passed into initial DebugState |
| `LOCAL_DEV` | ❌ | `false` | Set `true` to skip Service Bus and print to stdout |

## Comparison: Polling (Old) vs Event-Driven (New)

| | MonitorAgent (polling) | Azure Blob Trigger (new) |
|---|---|---|
| **Trigger mechanism** | `while True` + `sleep(300s)` | Blob Storage event (instant) |
| **Latency** | Up to 5 minutes | < 5 seconds |
| **Infrastructure cost** | VM/container running 24/7 | Consumption plan (pay-per-invocation) |
| **Missed events** | Possible (between polls) | Never (event-driven) |
| **Output** | Returns `DebugState` directly | Publishes JSON to Service Bus |
| **Scalability** | Single process | Horizontally scales automatically |
