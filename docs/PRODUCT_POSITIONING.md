# BeeSync Product Positioning

## What BeeSync is

BeeSync is a local Windows desktop Agent.  The C# desktop application starts a
local Agent Runtime and uses it to complete authorised work on the same Windows
computer: chat-directed tasks, local files, Windows applications, Revit and
user-approved local extensions.

The core relationship remains:

```text
Agent decides the task approach
Skill supplies domain guidance and scoped capabilities
Tool performs a specific, deterministic action
```

## Default execution boundary

- Execution occurs on the user's local Windows host.
- The Agent Runtime and Revit plugin listen on loopback addresses only.
- C# owns user identity, permissions, conversation records, secrets and user
  approval for extensions.
- Runtime receives only the context and credentials needed for the active Run.

## Explicit non-goals

The shipped desktop Runtime does not depend on Docker, Daytona, containers,
VNC, cloud sandboxes or remote code-execution infrastructure.  These are not
fallback mechanisms for local tasks.

If isolation becomes necessary for a future high-risk extension, it must be
designed as a separately enabled product capability with clear permissions,
auditing and deployment instructions.  It must never silently replace the
default local Windows execution model.

## Development decision rule

When implementing a capability, first ask: "Can this operate directly on the
authorised local Windows host through a declared Tool, Skill script or MCP
bridge?"  If yes, use that path.  Do not add a sandbox merely because a generic
Agent framework happens to support one.
