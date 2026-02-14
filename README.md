# Autonomous C# Debugging Agent (Azure OpenAI)

An AI-powered system using **Azure OpenAI (GPT-4o/GPT-4.5)** that automatically detects, analyzes, fixes, and creates PRs for C# errors in Azure Functions.

## Features

- **Azure OpenAI Integration** - Uses GPT-4o/GPT-4.5 from Azure AI Foundry
- **Error Detection** - Monitors Application Insights
- **Code Analysis** - Parses C# stack traces and code
- **Fix Generation** - AI-powered fix generation
- **Test Validation** - Runs dotnet test
- **PR Creation** - Creates draft PRs in Azure DevOps

## Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Fill in your Azure credentials:
- Azure OpenAI endpoint and API key
- Azure DevOps PAT
- Application Insights credentials

### 3. Run
```bash
python main.py
```

## Architecture

- **5 Specialized Agents**: Monitor, Analyzer, CodeGen, Tester, PR Creator
- **LangGraph Orchestration**: State machine with conditional routing
- **Azure OpenAI**: GPT-4o/GPT-4.5 for code generation

## Configuration

Key settings in `.env`:
- `AZURE_OPENAI_DEPLOYMENT`: Model to use (gpt-4o, gpt-4o-mini, gpt-35-turbo)
- `MAX_ATTEMPTS`: Maximum retry attempts (default: 3)
- `POLLING_INTERVAL_SECONDS`: How often to check for errors (default: 300)

## License

MIT
