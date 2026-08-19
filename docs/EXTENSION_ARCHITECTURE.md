# BeeSync Skill and Capability Architecture

## Product positioning (non-negotiable)

BeeSync is a **local Windows desktop Agent**.  It carries out authorised work
on the user's own Windows host: local applications, files, the Revit plugin and
user-approved local extensions.  It is not a cloud sandbox product and its
normal Runtime must not require or start Docker, Daytona, containers, VNC or a
remote execution environment.

This is a product boundary, not just a deployment preference.  New capabilities
must target the local Windows host by default.  If future work needs an isolated
execution environment, it requires a separate product decision, an explicit
user-facing permission design, and must not silently change the desktop Runtime
behaviour.

## Runtime model

BeeSync keeps the Agent general-purpose.  A Run starts with ordinary tools and
the Skill Catalog (name plus description only).  The model chooses a relevant
Skill by calling `activate_skill`; Python never routes a task through keywords.

```text
Agent + Skill Catalog
  -> activate_skill(id)
     -> SKILL.md instruction
     -> scoped MCP tools / Runtime workflows / script tools
```

`SKILL.md` is compatible with the common Agent Skills shape: it requires only
`name` and `description` front matter.  BeeSync execution declarations live in
the optional adjacent `beesync.skill.yaml` manifest.

## Capability boundaries

- **Agent** chooses Skills and combines capabilities for the user's goal.
- **Skill** provides domain instructions, references and a declared capability
  scope.  It does not automatically execute code.
- **Workflow** owns deterministic, high-volume domain orchestration.
- **MCP** bridges an external service and retains typed atomic operations.
- **Script Tool** executes one manifest-declared `run(**kwargs)` function; it
  cannot run arbitrary commands or install packages.

The Revit MCP Server deliberately continues to expose all atomic Revit tools to
external MCP clients.  BeeSync's Agent only receives the high-level Revit tool
subset after `revit-ifc-assignment` is active.

## Launch modes

| Entry point | Revit bridge behavior |
| --- | --- |
| `python main.py` | Defers the configured Revit SSE MCP until the Revit Skill activates. |
| `BeeSync.AgentRuntime.exe` | Defers the internal stdio Revit MCP child until the Revit Skill activates. |
| `python run_mcp_server.py --transport sse` | Runs only the standalone atomic Revit MCP bridge. |

## Extension admission

User Skill and MCP extensions are disabled until the desktop client validates
and explicitly enables them.  A valid executable Skill declares scripts,
schemas, MCP bindings and permissions in `beesync.skill.yaml`.  New versions
only affect new Runs; official packages cannot be overwritten by user packages.
