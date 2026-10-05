# One-PC validation evidence (2026-10-05 UTC)

Canonical project: `browser_use`. Canonical implementation Request:
`req_19671f74f2f649cbad7f6de5a3cdcc6a`.

Initial HEAD, origin/main and stated baseline were all
`7be96ed8bafa8dfe1eef228b59cf5c884b8b2431`. Working tree was clean.
GitHub confirms fork parent `browser-use/browser-use`, default branch main.
No RAON PR or matching implementation branch was found. Native request-scoped
control initially reported no child Requests. There is no global canonical
Request listing interface in this execution context, so unrelated concurrent
Requests were not exhaustively audited. AS-IS work ID is retained in README;
implementation decisions were checked directly against current repository code.

## Source evidence and implementation boundary

Native `browser_use/agent/service.py`: Agent constructor accepts browser_session,
output_model_schema, file_system_path, bounded timeouts and enable_signal_handler;
`Agent.run` implements the existing loop and its own retries. Native
`browser_use/browser/session.py`: start, get_browser_state_summary, CDP access,
take_screenshot and kill supply browser lifecycle/DOM/evidence. Native
`browser_use/tools/service.py`: Tools.act delegates validated dynamic action
models through Registry.execute_action. Native history supplies terminal result,
structured output, URLs and action/results. Adapter pairs executed results with
actions and excludes raw parameters. No changes to these source files, the model
adapters, upstream history, MIT LICENSE or other projects were needed.

Plan implemented: private worker wrapping native APIs; typed bounded CLI input;
atomic run reservation; minimal result/evidence projection; process cancellation
and bounded cleanup; native local fixture; separate model-blocked result. No
AgentOpt architecture was copied. Only `.gitignore`, `browser_use/raon/` and
`tests/raon/` are changed.

## Actual checks and exit statuses

| Check | Actual result |
| --- | --- |
| Adapter contracts/lifecycle/native-browser tests | 20 passed, exit 0, 39.27 s |
| Upstream focused regression | 43 passed, exit 0, 69.71 s |
| pre-commit, every applicable configured hook | PASS, exit 0 |
| Explicit pyright over adapter and tests | 0 errors, exit 0 |
| uv build, sdist and wheel | PASS, exit 0; wheel includes all five adapter modules |
| Fixed code commit internal fixture CLI | COMPLETED, exit 0 |
| Real Agent CLI credential boundary | REAL_BROWSER_AGENT_LLM_BLOCKED, exit 2 |

Adapter command:

```bash
RAON_DISABLE_SANDBOX=1 \
RAON_BROWSER_EXECUTABLE=/home/ubuntu/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
ANONYMIZED_TELEMETRY=false BROWSER_USE_CLOUD_SYNC=false \
.venv/bin/pytest tests/raon -o addopts='' -o log_cli=false -q
```

Upstream regression command (test process only):

```bash
IN_DOCKER=true TMPDIR=/proc/self/cwd/.raon-runs/regression-tmp \
BROWSER_USE_CONFIG_DIR="$PWD/.raon-runs/regression-config" \
ANONYMIZED_TELEMETRY=false BROWSER_USE_CLOUD_SYNC=false \
.venv/bin/pytest tests/ci/test_action_timeout.py \
tests/ci/test_registry_empty_url_domain_filter.py \
tests/ci/infrastructure/test_registry_core.py \
tests/ci/infrastructure/test_registry_validation.py \
-o addopts='' -o log_cli=false -q
```

An earlier default-sandbox regression invocation was interrupted after Chromium
launch failures: 46 passed, 15 setup errors, exit 2. It is not counted as PASS.
Reproduction with the environment-only sandbox setting above passed all 43
selected upstream cases. Initial adapter experiments also exposed Chromium's
Unix socket length limit; the private worker cwd/TMPDIR strategy fixes it without
changing Browser-Use core or using global temp profiles for product runs.

## Fixed commit one-PC runtime smoke

Code commit: `5965cb7921e25c23cfbbddf8bd35488b5d0d6c00`.
Run ID: `one-pc-release-5965cb792`.
Browser: actual user-installed `Chrome/151.0.7922.34`, headless, isolated profile.
Model execution: `NONE_DETERMINISTIC`; this is not real Agent inference.
Native Tools navigation and DOM-indexed click, then fixture extraction returned
`RAON fixture`, `[Alpha, Bravo, Charlie]`, `Action complete`.

Retained evidence is under `evidence/fixture/`: metadata, result, bounded actions,
and one 18,033-byte screenshot. Metadata records SHA-256 and sizes. Versioned JSON snapshots have the trailing
newline required by pre-commit; their artifact digests were recalculated after
that whitespace normalization. The screenshot bytes are unchanged. The screenshot
is a nonsensitive fixture. Tests validate evidence schemas/digests and known secret
controls. Product output retains no cookies, authorization headers, raw model
payload, raw DOM, HAR/video or profile data. `evidence/model-blocked/` records the
separate real Agent attempt: no selected inference credential in the Request
execution environment, no browser launch, `NOT_RUN`. No platform credentials,
personal profile or new API keys were accessed.

Runtime smoke commands:

```bash
RAON_DISABLE_SANDBOX=1 RAON_BROWSER_EXECUTABLE=/home/ubuntu/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome \
.venv/bin/python -m browser_use.raon run --task-file .raon-runs/release-task.json
.venv/bin/python -m browser_use.raon run --task-file .raon-runs/real-task.json
```

The fixture task file used mode fixture, controlled_page/capture_screenshot true,
loopback allowlist and a read/click/extract task. The real task file used mode agent,
https://example.com, example.com allowlist, read-only title task and title:str
output schema. Their run IDs are those in the retained metadata. Inputs were
request-local temporary files, not credentials or arbitrary sample programs.

After smoke, native worker exit status was 0, cleanup PASS, private directory
absent and no process command line referenced the run path. Native cancellation
and timeout tests additionally observe Chromium before interruption/timeout and
assert no run process/private profile remains. Reusing a reserved ID fails instead
of replaying external actions. No daemon or public port was created.

Native control runtime identities were identical before and after internal smoke:
backend `fd18e5151b0b1bcee20e0af05eb73cfdcb12e5e0`, web
`0dbcfd477ebbd95aa007c66e3eb5719193e622fb`, Agent.Tools
`7071d2c2ed911a9456d76845d6d795d5aad976d5`. No service restart, capacity change,
project configuration mutation or interruption of another Request was performed.
The native catalog reports the Request's pinned source revision separately from
Git's actual adapter checkout revision; retained metadata uses actual Git HEAD.
The current canonical Provider Request continued throughout these commands.
Other active Requests are not enumerated by the available request-scoped tool.

## Delivery status at this evidence commit

- ADAPTER_CODE: PASS.
- LOCAL_BROWSER_FIXTURE: PASS, actual Chromium, no model.
- REAL_AGENT_E2E: REAL_BROWSER_AGENT_LLM_BLOCKED, not PASS.
- EVIDENCE: PASS, minimal fixture snapshot retained in Git.
- ONE_PC_RUNTIME_SMOKE: PASS on fixed code commit, cleanup verified.
- INDEPENDENT_REVIEW: pending fixed-commit cross-provider review.
- GIT_INTEGRATION: dedicated branch; PR/CI/merge outcome will be recorded after review.

No Windows runner, remote screen viewing, repeated RPA scheduler/task UI or login
automation is implemented. Next priorities: Windows 2PC Local Runner, then
recurring RPA Tasks using the existing contract.
