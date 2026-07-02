"""
tools/
======

WHAT THIS PACKAGE DOES
----------------------
Contains all LangChain `@tool`-decorated callables and helper classes used by the
sub-agents inside the **autonomous debugging pipeline**.  Each module groups tools
by the agent that uses them, making it straightforward to extend or replace a single
agent's capabilities without touching the others.

MODULES
-------
investigation_tools
    Tools for the **Investigator Agent**: reading files, grepping the codebase,
    inspecting git history, parsing C# stack traces, and mapping directory structure.

fixer_tools
    Tools for the **Fixer Agent**: reading and writing source files, running
    `dotnet build`, and searching for symbol usages before applying a patch.

teams_notifier
    A lightweight adapter for sending formatted alert cards to a Microsoft Teams
    channel via an Incoming Webhook URL.  Used at key pipeline milestones (exception
    detected, PR created) without requiring any Azure AD OAuth setup.
"""
