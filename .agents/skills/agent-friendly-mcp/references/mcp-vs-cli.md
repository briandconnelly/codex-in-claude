# MCP vs CLI: Choosing the Surface

This is a decision aid for authors deciding how to expose capability to an agent. The wrong question is "modern vs old"; the right question is "what contract does the agent actually need?" Where the agent lives, how many capabilities you have, and how output flows back into context all change the answer. The same behavioral core can be exposed through any of the surfaces below — the choice is about fit, not fashion.

## Three Targets

### Direct tool-calling MCP

Typed tool schemas with descriptions and annotations, semantic discoverability across multi-server clients, and protocol-level support for resources and prompts.
The chat surface lists tools, the model picks one, and arguments are declared in a schema that capable clients validate before invocation — though client validation is not guaranteed, so the server still validates every call itself.

**When it fits.** The agent lives in a chat surface that can list and call typed tools. Selection from many candidates matters, and the surface itself benefits from the model rendering arguments and reading annotations (read-only, destructive, idempotent) at call time. You want clients to differentiate read-only from destructive operations without you parsing intent yourself.

**Decision hinge.** The agent benefits from typed args and semantic discovery in the chat UI itself. If the UI never surfaces tool metadata to the model — or if there's only one obvious entry point — the typed schema isn't earning its weight.

**Cost.** Token-heavy at scale: preloading clients pay for every definition before the agent does any work, so compact definitions and catalog reduction are the levers — see [contract-checklist.md §2](contract-checklist.md).

### Code-execution with MCP

The agent writes code against the MCP server presented as a code API, executes it in a sandbox, and reads the result. Tool definitions live as files on a filesystem the agent can list and load on demand; intermediate values stay in the sandbox instead of round-tripping through the model's context.

**When it fits.** The agent has a code-execution sandbox. You have many capabilities — dozens to hundreds of tools, or large resource catalogs — and intermediate results are big or sensitive. The agent wants to filter, join, and chain operations without each intermediate value entering the prompt. Treat servers as code APIs: the agent imports them, calls them, and composes them like any other library.

**Decision hinge.** Filtering and chaining in the model context is wasteful or unsafe. If the agent's natural move is "fetch a thousand records, keep the five that match, summarize," doing that in-environment is dramatically cheaper than streaming everything back through tool calls. Filesystem-style discovery — the agent lists definitions, loads only what it needs — replaces the upfront tool-loading tax.

**Cost.** Needs sandboxing infrastructure and a runtime that can present MCP servers as code APIs. The agent has to know the code API conventions (how servers are imported, how auth is threaded, where definitions live on disk). Errors now cross a code-execution boundary, so error taxonomy has to translate cleanly into language-native exceptions. See Anthropic's "code execution with MCP" framing for the underlying pattern.

### CLI

A shell-composable command with stable text contracts on stdout, diagnostics on stderr, exit codes that classify outcomes, and conventional flags. Fits naturally inside code-execution loops where the agent is already shelling out for everything else.

**When it fits.** A small set of obvious entry points; the agent can shell out; stdout/stderr semantics and exit codes are sufficient to convey what happened. Discovery overhead is low — `--help` and a handful of verbs are enough — and the agent is composing with grep, jq, and existing scripts, not with a typed-tool registry.

**Decision hinge.** Typed schemas don't earn their cost. If the surface is small, the operations are obvious, and the agent's environment already speaks shell, a CLI is the lowest-overhead choice and integrates with everything else the agent does in that environment.

**Cost.** Weaker discoverability when the capability count grows; less ergonomic in chat UIs that don't expose a shell to the model. Read-only vs destructive distinctions live in conventions and exit codes rather than typed annotations. Defer to the **agent-friendly-cli** skill for detailed CLI guidance.

## Decision Hinges

| Dimension          | Direct MCP            | Code-exec MCP          | CLI                    |
| ------------------ | --------------------- | ---------------------- | ---------------------- |
| Agent location     | chat                  | code-exec              | code-exec / shell      |
| Capability count   | few–moderate          | many                   | few                    |
| Discovery needs    | semantic              | progressive (on disk)  | none / `--help`        |
| Mutation signaling | typed annotations (`readOnlyHint`, `destructiveHint`) | language exceptions + typed returns | exit codes + stderr + flag conventions |
| Output size        | small                 | large / streamed       | small / streamed text  |
| Selection pressure | many candidates, model picks | agent code picks, by name | agent picks one verb |

If a row's answer is "either," the dimension isn't forcing a choice — look at the next row.

## One Behavioral Core, Multiple Surfaces

These three are not exclusive. A stable behavioral engine can fan out to a CLI, an MCP server, and a code-execution-friendly API simultaneously. Choose surfaces based on where the agent lives, not what's modern.

When you do build more than one surface, the elements that must be **shared** across them:

- **Auth model.** Same credential types, same scopes, same failure modes. An agent that learned how to authenticate against the CLI should not have to relearn against the MCP server.
- **Error taxonomy.** Same stable error codes, same retryability semantics, same field-level repair signals. A "missing credential" is the same class of failure whether it surfaces as an MCP error payload, a non-zero exit code with a stderr message, or a language-native exception.
- **Versioning fingerprint.** A single capability fingerprint covers the underlying behavior. Surfaces may add their own surface-version on top, but the behavioral version is one number — clients on any surface detect breaking changes the same way.

What may legitimately differ: argument shape (typed JSON vs flags vs function args), output rendering (structured response vs stdout text vs return value), discovery mechanics (catalog vs filesystem vs `--help`). These are surface concerns. The contract underneath them is one contract.
`_meta` namespacing is itself a surface concern: it is MCP-specific, so keep the semantic error codes and metadata identical across surfaces but let each carry them in its own envelope — on MCP, `tools/call` failures use the tool-result error (`isError: true` with structured fields like `code` and `repair`, see §6) and non-tool RPC methods use JSON-RPC `error.data`, with `_meta` reserved for metadata extensions; a CLI uses exit codes plus stderr; a code API uses language-native exceptions.

## Anti-patterns

- **Choosing MCP because it's new**, when a stable CLI already serves the same agent well. The agent doesn't care what year the surface was invented; it cares whether the surface fits its environment.
- **Wrapping a CLI as MCP without rethinking primitives.** You inherit the CLI's surface flaws as tool definitions — endpoint-mirroring becomes verb-mirroring, and the agent ends up chaining MCP calls that map one-to-one onto subcommands. Redesign for tasks (see [contract-checklist.md §3](contract-checklist.md)).
- **Building both surfaces with divergent error taxonomies or version semantics.** Two truths means the agent has to learn each surface from scratch and can't share repair logic across them.
- **Building MCP for a code-execution-only agent that would prefer a typed library.** If the agent never lists tools through a chat UI and is always writing code anyway, an SDK in its language — or the code-execution-with-MCP pattern — is a better fit than direct tool-calling MCP.
