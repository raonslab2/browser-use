# RAON V2: one-PC Browser-Use adapter

This additive integration belongs to canonical project `browser_use` in
`raonslab2/browser-use`. The fork and MIT license are unchanged. It calls the
original `Agent.run`, `BrowserSession`, `Tools` and Registry; it does not implement
an AgentOpt provider, Request database, scheduler or inference service.

Baseline: `7be96ed8bafa8dfe1eef228b59cf5c884b8b2431`, Browser-Use 0.13.10,
Python >=3.11. AS-IS input: `work-20261005-browser-use-as-is-v2-input-9ba612e4`.

## Bootstrap and CLI

Use a project-specific environment, never another project's venv or system pip:

```bash
uv venv --python 3.11
uv pip install -e . pytest pytest-asyncio pytest-xdist pytest-timeout pytest-httpserver pre-commit
```

Reuse an existing user-installed Chromium by setting `RAON_BROWSER_EXECUTABLE`
to its executable. If none exists, follow the repository's native browser
installation procedure in this isolated environment. Do not upgrade system
packages. The sandbox remains enabled by default. On a restricted Linux host
without usable Chromium sandbox support, explicitly set `RAON_DISABLE_SANDBOX=1`
for the controlled fixture only; this setting is not a production security boundary.

Create a task JSON inside the Request workspace, with no credentials:

```json
{
  "task": "Read the fixture",
  "run_id": "my-first-fixture",
  "allowed_domains": ["127.0.0.1"],
  "mode": "fixture",
  "headless": true,
  "max_steps": 10,
  "timeout": 120,
  "controlled_page": true,
  "capture_screenshot": true
}
```

```bash
.venv/bin/python -m raon_browser run --task-file task.json --output .raon-runs
```

Run from the project workspace. Output must be a relative path resolving strictly
inside that workspace, including through existing symlinks. An atomic run directory
reservation rejects an existing run ID. CLI exit 0 means COMPLETED; terminal
failure, cancellation and missing model credentials exit 2. Validation errors do
not start a browser. Request results can consume the printed status and metadata
location directly. No long-lived server or external listening port is created.

The input schema is `RunInput`. `start_url` is optional HTTP(S), without userinfo.
Host allowlists accept explicit hostnames and `*.hostname` patterns; native
BrowserSession domain enforcement remains authoritative. Fixture mode ignores
external URLs and creates its own ephemeral loopback-only fixture with a native
DOM-indexed click. It uses **no model** (`NONE_DETERMINISTIC`), so its success is
never REAL_AGENT_E2E=PASS. The fixed fixture deliberately has no login, form
submission, download, payment or external mutation.

For real Agent execution, set `mode: "agent"` and supply a public or controlled
read-only task. Default inference adapter is `ChatBrowserUse`, using an existing
`BROWSER_USE_API_KEY` from the execution environment. Explicit `openai`, `anthropic`
and `google` selections use their corresponding existing environment keys and
require the caller's exact model name. No model names are rewritten. Development
CODEX/CLAUDE accounts do not provide Browser-Use inference credentials.
The top-level `raon_browser` bootstrap sets `PYTHON_DOTENV_DISABLED=1` before
importing Browser-Use, and the supervisor also sets it for its worker. This uses
the pinned python-dotenv suppression setting without modifying upstream code.
The worker runs in a private directory and does not load a repository `.env` or
personal browser profile. Direct `python -m browser_use.raon` execution is retired
and refuses to start a run: that package imports upstream before it can enforce
the dotenv boundary. For library callers, disable dotenv before the first
Browser-Use import; use the top-level CLI for the supported credential boundary. A missing selected credential yields
`REAL_BROWSER_AGENT_LLM_BLOCKED` with `NOT_RUN`; it creates no browser and does
not fall back to synthetic inference.

`output_schema` optionally maps flat field names to `str`, `int`, `bool` or
`str_list`, for example `{"title":"str","items":"str_list"}`. The worker compiles
this to a Pydantic v2 model and passes it as native `output_model_schema`.
`final_result` and validated `structured_result` remain distinct. This first-wave
CLI schema intentionally supports a small, portable subset.

## Evidence and lifecycle

Each run retains `metadata.json`, `result.json`, `actions.json`, and optionally
one `last.png`. Metadata includes terminal status, failure stage/type, actual
checkout/adapter revisions, native browser version, model identity, worker exit
status and SHA-256/size for retained result/action/screenshot artifacts. Metadata
cannot hash itself. Action names are bounded to 100, with no parameters or raw
model responses. Real-agent action names are paired with native results to omit
unexecuted proposed actions. URL summaries retain origin only.

Screenshot retention requires `controlled_page: true`; use it only for fixtures
or verified public nonsensitive pages. This is an explicit operator attestation,
not a content classifier. Never run login/account tasks in this wave. Basic text
controls redact known environment credential values, sensitive structured keys,
Bearer tokens, obvious credential assignments and URL paths/query/userinfo.
These controls are not a general PII or arbitrary secret detector: do not put
secrets into task text or process nonsensitive-looking private records. Upstream
history, DOM, cookies, profiles, downloads, screenshots used internally by the
Agent, and raw model errors are confined to `.private` and deleted after execution.
No video, HAR, conversation trace or raw upstream log is retained. Worker stdout
and stderr are discarded. Exception messages are omitted from product evidence.

The supervisor creates a separate Linux worker session. SIGINT/SIGTERM stop
execution; worker cancellation invokes native Agent stop and BrowserSession kill.
Timeout stops the worker without any product retry and records
`TIMEOUT_ACTION_OUTCOME_UNKNOWN`. A bounded cleanup grace follows the execution
limit; the supervisor kills remaining members of its own session and checks for
live processes before reporting cleanup. Native step/LLM retries remain upstream
behavior and are distinct from future canonical Request retries. No restart
recovery or durable resume is claimed. Abrupt SIGKILL/power loss cannot run Python
cleanup; the run directory reservation still prevents replay of that ID.

The worker's cwd is its request-private directory. `TMPDIR=/proc/self/cwd/tmp`
provides a short Linux socket path while keeping all data under the workspace.
This avoids Chromium's Unix socket length limit in deeply nested Request paths.
Native profile copying is allowed only within that private temp root. A future
Windows runner must replace this Linux process/temp strategy while retaining the
input/result contract and native Browser-Use execution.

## Validation

```bash
PYTHON_DOTENV_DISABLED=1 ANONYMIZED_TELEMETRY=false BROWSER_USE_CLOUD_SYNC=false \
  .venv/bin/pytest tests/raon tests/ci/test_action_timeout.py \
  tests/ci/test_registry_empty_url_domain_filter.py \
  tests/ci/infrastructure/test_registry_core.py \
  tests/ci/infrastructure/test_registry_validation.py \
  -o addopts='' -o log_cli=false -q
.venv/bin/pre-commit run --files raon_browser.py pyproject.toml browser_use/raon/*.py tests/raon/test_adapter.py
```

Set `RAON_BROWSER_EXECUTABLE` for the real browser integration tests; without it
they explicitly skip. Telemetry/cloud sync are disabled by the product worker
configuration; original telemetry code is preserved. Schema, path escape, basic
secret controls, native structured output, duplicate reservation, model-blocked
status, timeout cleanup, actual browser click/evidence and SIGTERM cleanup are
covered. Consult `VALIDATION.md` for actual run results and limitations.

Windows execution, remote viewing, recurring RPA, task editors and personal
cookie/login integrations are future waves. Recommended next order: Windows 2PC
Local Runner first, then recurring RPA Task execution using this same contract.
