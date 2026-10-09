---
name: antigravity-cli
description: Drive the Antigravity CLI (`agy`) for non-interactive agentic runs - single prompts, JSON/stream-json output, model and effort selection, conversation resume, and MCP/plugin wiring. Use when asked to run, delegate to, or script Antigravity/Gemini from the shell.
---

# Antigravity CLI (`agy`)

Wrapper around Antigravity's agent runtime. Every behaviour below was verified by live
execution, not from documentation. Version at time of writing: `1.2.16`, a stripped
ELF binary at `~/.local/bin/agy`.

## Purpose

To drive `agy` non-interactively and safely from a script or another agent, without
mistaking a reported success for completed work.

## Inputs

- A working directory for the task (the `agy` process runs in its cwd).
- A prompt string.
- Tool permissions configured (see [Permissions](#permissions-headless--unattended-use)).

## Preflight

```bash
command -v agy && agy --version
```

Do not assume a previous run's success. The service returns 503 intermittently (see
[Failure Modes](#failure-modes)) — a clean-looking bare invocation can still have failed.

## Core: one-shot non-interactive run

**Flag order matters.** `-p` takes the *next argument* as the prompt. Putting another
flag after `-p` silently hijacks it:

```bash
agy -p "say OK" --output-format json
# error: -p took "--output-format" as its prompt, so the intended prompt
#        was left as an argument and ignored.
```

Put every flag before `-p`, or use `-p="your prompt"`:

```bash
agy --output-format json -p "say OK"
```

Always pass `timeout` when scripting:

```bash
cd /path/to/work && timeout 120 agy --output-format json -p "<prompt>" > out.json 2> err.txt
```

**No output yet is not failure — do not kill on that heuristic.** Observed: a run piped
through `head` stayed silent for the full window and was killed (`error: context
canceled` — the kill signal, not an Antigravity error), while an identical run redirected
to a file completed successfully. Output is buffered until the process exits. Let the
timeout govern; branch on `status` in the JSON instead.

## Output formats (`--output-format`)

| Value | Use |
|---|---|
| `text` | Default. Human-readable final response on stdout. |
| `json` | **Use this for anything scripted.** One JSON object, always emitted. |
| `stream-json` | NDJSON, one message per line. Pairs with `--input-format stream-json`. |

`json` output shape (observed):

```json
{"conversation_id":"16d5ce6c-...","status":"SUCCESS","response":"OK\n",
 "duration_seconds":1.97,"num_turns":1,
 "usage":{"input_tokens":11761,"output_tokens":22,"thinking_tokens":21,
          "cache_read_tokens":0,"total_tokens":11783}}
```

`status` is `SUCCESS` or `ERROR`. But **`SUCCESS` does NOT mean the task was done** — see
[Failure Modes](#failure-modes). Errors are printed to stderr *and* reflected in the JSON,
and the process can exit ambiguously when piped. Parse stdout as JSON; do not rely on `$?`.

A `json` result can also carry a `denied_actions` array listing tool calls that were
refused. **`SUCCESS` + empty `response` + non-empty `denied_actions` means the model ran,
wanted a tool, was blocked, and gave up — all while reporting success.**

Substantial system/tooling context is preloaded (~11.7k input tokens on a two-word
prompt). Assume each call is not free, and batch related questions into one prompt.

## Structured output

`--json-schema` takes a schema **string or a path to a schema file** and constrains the
result. With `stream-json` it applies only to the final result.

## Useful flags

| Flag | Effect |
|---|---|
| `--model <name>` | Pin a model (see below). |
| `--effort <lvl>` | `low` \| `medium` \| `high` \| `xhigh` \| `max` |
| `--mode <m>` | `accept-edits` (auto-apply edits) or `plan` (read-only). **Prefer `plan` when scoping.** |
| `--sandbox` | Run with terminal restrictions enabled. |
| `--dangerously-skip-permissions` | Bypasses tool permissions. **Do not use.** |
| `--add-dir <path>` | Widen the workspace; repeatable. |
| `--continue` / `-c` | Continue the most recent conversation. |
| `--conversation <id>` | Resume by conversation ID (take it from `conversation_id`). |
| `--new-project` | Fresh project context. |
| `--project <id\|name>` | Pin project. |
| `--print-timeout <dur>` | `0` = wait indefinitely (default). Set one. |
| `--log-file <path>` | Override log path. |
| `--disable-slash-commands` | Disable slash-command/skill expansion in print mode. |

## Models

`agy models` fetches live and requires network:

```
gemini-3.8-flash-{high,medium,low}      gemini-3.7-flash-{high,medium,low}
gemini-3.6-flash-{high,medium,low}      gemini-3.1-pro-{high,low}
claude-opus-5-5-{low,medium,high}       claude-sonnet-5-5-{low,medium,high}
gpt-oss-120b-medium
```

Tier is baked into the model id. Cheap default: `gemini-3.8-flash-low`. Heavy reasoning:
`claude-opus-5-5-high`.

`agy agent` / `agy agents` list available agents — **observed to return empty**. Do not
build on it.

## Subcommands

```
agy agent|agents      List available agents          agy models     List available models
agy changelog         Release notes                   agy update     Update CLI
agy install           Configure PATH / shell aliases
agy mcp               add | remove | list | enable | disable
agy plugin            list | import [gemini|claude] | install | uninstall | enable
                      | disable | validate | link
agy remote-control    start | status | stop
```

`agy plugin import` pulls plugins from Gemini or Claude configs — a migration path worth
checking before hand-authoring tooling.

## Permissions (headless / unattended use)

Headless mode cannot prompt for tool permissions. It may return status SUCCESS even when a required action was denied; inspect the denied_actions array and verify the side effect independently.

Configuration lives at ~/.gemini/antigravity-cli/settings.json. State and logs live alongside it. Settings persist across every run on the machine. Back up that file before a permitted edit, and review the current trustedWorkspaces and permissions.allow values first.

Grant only the workspace and exact tool patterns needed for an authorized task. Do not add a whole-home trusted workspace or blanket command(*), write_file(*), or delete_file(*) permissions. Do not use --dangerously-skip-permissions. If the task needs broader access than its approved scope, use read-only plan mode or obtain a scoped configuration change through the normal authority path.

Tool permissions are per-tool: command access does not imply write_file or create_file access. Observed tool names include command, write_file, create_file, delete_file, list_dir, and search. After changing permissions, run a bounded task and confirm both denied_actions and the actual file or command result.

## Failure Modes

**`status: SUCCESS` can mask a completely failed task.** Observed twice, both times
reporting success with an empty `response`:

```json
{"status":"SUCCESS","response":"","num_turns":1,
 "denied_actions":[{"action":"command","display_name":"RunCommand"}]}
```

Headless mode auto-denied the tool, the model gave up, and status stayed `SUCCESS`. Stderr identifies the missing permission. Scope a permitted rule to the task; do not use the broad bypass that the CLI may suggest.

A denied run may still spend real tokens (~24k input observed) — a denial is not free.

**503 eligibility errors are routine and intermittent.** Observed back-to-back, same
host, same command: attempt 1 `SUCCESS`, attempt 2 `UNAVAILABLE (code 503)`.

```
error: Eligibility check failed: UNAVAILABLE (code 503): The service is currently unavailable.
```

This is upstream, not local. **Retry up to 3 times with a short backoff.** If it still
fails, report the failure — do not silently return empty output as if the task succeeded.

**`agy --help` output is non-standard.** It prints a bare `Usage of agy:` header from Go's
flag package with no short-form grouping. It does list every subcommand, so it remains
worth reading; per-subcommand `--help` is conventional and cleaner.

## Verified vs unverified

**Verified by live execution:** version; full flag list; subcommand list;
`--output-format json` shape and its `denied_actions` field; the `-p` flag-order gotcha;
model list; intermittent 503s; empty `agy agent`; output buffering; `settings.json` path
and schema; per-tool permission granularity; and **real unattended work** — file creation
and a bug fix, both confirmed by independent inspection and execution.

**Not verified — do not assume:** `--sandbox` behaviour, `--dangerously-skip-permissions`
(the flag form was never needed once `settings.json` was configured),
`--input-format stream-json`, `--json-schema`, `--add-dir`, `--project`/`--new-project`,
`agy install`, `agy remote-control`. Treat these as documented-only until exercised.

## Cost

Input tokens are heavy and scale with task complexity, not prompt length:

| Task | input | output | thinking |
|---|---|---|---|
| two-word prompt | 11.7k | 22 | 21 |
| one-line file create | 37k | 921 | 725 |
| read + edit + run a file | 104k | 2.6k | 1.8k |

`cache_read_tokens` reached 20k on the edit task, so repeated runs within a session do get
cache discounts. Budget accordingly; do not poll with trivial prompts.

## When to reach for a separate agent instead

This CLI is a **tool**, not an agent. Keep it behind a skill. Escalate to a dedicated agent
only when you need different model routing, a separate workspace, a hard security
boundary, or its own autonomous schedule.

## Checklist

Before declaring an `agy` task complete, confirm all of the following:

- [ ] `status` is `SUCCESS` **and** `response` is non-empty.
- [ ] `denied_actions` is absent or empty.
- [ ] **The side effect was verified independently** — `ls`, `cat`, `test -e`, running
      the code, or a diff. Never treat `status` alone as proof of work.
- [ ] No `Eligibility check failed` / 503 text in stderr.
- [ ] If the run was killed by `timeout`, re-run it redirected to a file rather than
      through a pipe, and do not read the silence as failure.

## Token / Performance Optimization

- **Batch related questions** into one prompt. Input tokens are ~11.7k even for a 2-word prompt (preloaded system/tooling context). A single multi-part prompt costs far less than N separate calls.
- **Use `--continue` / `-c`** for iterative work on the same conversation. It reuses cache (observed `cache_read_tokens` ~20k) rather than rebuilding the full context window.
- **Avoid trivial prompts.** A bare "say OK" still consumes ~11.7k input tokens + 21 thinking tokens. Only invoke when there is a concrete deliverable.
- **Prefer `gemini-3.8-flash-low`** (cheap default) over `claude-opus-5-5-high` unless the task needs heavy reasoning. The cost table above scales roughly 3-5x with task complexity regardless of prompt length.
- **Redirect output to a file** rather than piping through `head`. A kill signal through a pipe produces `context canceled` (not an `agy` error) and wastes tokens.
- **Retry 503 errors up to 3 times** with short backoff. A retry costs tokens but less than a manual recovery cycle. Do not silently return empty output.