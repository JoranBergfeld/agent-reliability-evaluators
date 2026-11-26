# Foundry & CI/CD distribution channels for a customer-facing evaluation offering with custom evaluators

- **Evidence date / access date:** 2026-11-26 (all sources below fetched on this date)
- **Question:** What distribution channels do Microsoft Foundry and common CI/CD platforms document for a customer-facing evaluation offering that includes custom evaluators?
- **Ticket:** https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/20
- **Map:** #1
- **Method:** web_fetch of Microsoft Learn, PyPI JSON API, GitHub raw files (microsoft/ai-agent-evals, Azure/azure-sdk-for-python), Visual Studio Marketplace. "Documented" = stated in a fetched primary source. "Inference" = my reasoning, not stated by a source.

## Summary answer

- **No third-party publishing channel is documented for evaluators.** Custom evaluators are registered per Foundry project (`evaluators.create_version`, REST "Create an evaluator version", or portal Evaluator catalog > Custom evaluator > Create). I found no doc for cross-project, cross-hub, Azure ML registry, Marketplace or partner listing of Foundry evaluators. An older Learn snapshot says evaluators are "managed in the evaluator catalog with the same project-scope, role-based access control". Treat evaluator distribution as "each customer registers into their own project", which can be automated by our package or script.
- **SDK/package channel is solid and stable.** `azure-ai-projects` 2.11.0 (2026-11-13, Production/Stable, Python >=3.10) and `azure-ai-evaluation` 1.18.12 (2026-11-09 per changelog; PyPI upload 2026-11-12, Production/Stable, Python >=3.9). Our own package can go to PyPI or a private feed. `evaluate()` accepts plain callables or classes as custom evaluators and can log results to a Foundry project.
- **Custom evaluator definitions are preview and need the opt-in.** Prompt, code and endpoint evaluators are documented as preview. Creation sends `Foundry-Features: Evaluations=V1Preview`. The evaluation run API (`openai_client.evals`, `azure_ai_evaluator` testing criteria) is documented without a preview banner. Rubric evaluators are the Microsoft-recommended new LLM-judge path, but rubric safety and task-level modes are preview.
- **Code evaluators are sandboxed.** Python function `grade(sample, item) -> float`. Limits: 256 KB, 2 min per call, no network, 2 GB RAM, 1 GB disk, 2 cores, a fixed package list (numpy 2.2.4 etc.), turn-level only. Microsoft now steers new custom code to **endpoint-based evaluators (preview)**: our own HTTPS endpoint, registered via a project connection, no sandbox limits. This is a materially different distribution model (we host, the customer registers).
- **CI/CD:** `microsoft/ai-agent-evals@v3-beta` GitHub Action (preview, only `v1/v2/v3-beta` tags, latest release 2025-12-23, last commit 2026-05-20) and the Azure DevOps task `AIAgentEvaluation@2` (extension `ms-azure-exp-external.microsoft-extension-ai-agent-evaluation`, tagged Preview). Both take an `agent-name:version` list plus a JSON data file whose `evaluators` can name catalog evaluators, so custom evaluators must already be registered in the customer project. A newer documented path is the `azd` evaluations extension (`azure.ai.evaluations` 1.0.0-beta.2) which can publish evaluators and gate on pass rate. Any CI job can run the SDK with OIDC/workload identity federation.
- **Prompt and hosted agents:** the cloud-evaluation agent target (`azure_ai_target_completions` + `azure_ai_agent` target) is documented for both prompt agents and hosted agents (responses protocol same code; invocations protocol uses a different `input_messages` format). The GitHub Action docs only say "Foundry agents" and use `agent-name:version`. Hosted-agent support in the action is **not explicitly documented**.
- **RBAC:** documented minimum for running evals (portal, SDK, CI/CD) is **Foundry User** on the project. Model-inference judge calls need Foundry User at the Foundry account scope. The docs I read do not state a distinct role for registering evaluators (see Unverified).

## 1. Foundry evaluator catalog / registry publishing

### Documented
- Custom evaluators are created and managed in the project's **evaluator catalog**, from the portal (Evaluation > Evaluator catalog > Custom evaluator > Create; types Prompt-based, Code-based, Endpoint-based) or the SDK. Source: Learn "Other custom evaluators (preview)" (ms.date 2026-11-11, updated 2026-11-24) and "Endpoint-based custom evaluators (preview)".
- SDK registration: `project_client.evaluators.create_version(name=..., evaluator_version={... "definition": {"type": EvaluatorDefinitionType.PROMPT|CODE, ...}}, headers={"Foundry-Features": "Evaluations=V1Preview"})` (docs require `azure-ai-projects>=2.8.0` for prompt/code, `>=2.11.0` for endpoint). Evaluators are versioned (`create_version`, `get_version(name, version="latest")`, `.beta.evaluators.list()`).
- Endpoint-based registration is two steps: (1) create a project connection (`azure-mgmt-cognitiveservices` `account_connections.create`, API key or Entra ID) holding the URL and credentials, (2) `create_version` with `EndpointBasedEvaluatorDefinition(connection_name=..., schema_version="0.0.2", metrics=...)`. The REST reference is "Create an evaluator version" (https://ai.azure.com/api-reference/evaluators/create-version/). Needs `Foundry-Features: Evaluations=V1Preview`. Timeout 120 s per item.
- Evaluation runs reference the evaluator by name: `{"type": "azure_ai_evaluator", "evaluator_name": "<name>", ...}` in `testing_criteria`.
- azure-ai-projects 2.11.0 added preview `.beta.evaluators.list_diagnostics()` "for evaluator publication lineage and diagnostics" (CHANGELOG 2.11.0). The word "publication" is not explained further in the changelog.
- No page I fetched describes sharing evaluators across projects, hubs, Azure ML registries, or a Marketplace/partner listing for evaluators. Web searches for those terms returned only the custom-evaluator pages.
- Older Learn snapshot (cloud-evaluation page, last_updated 2026-07-21, crawled 2026-08-03, via search snippet only): "Evaluators are managed in the evaluator catalog with the same project-scope, role-based access control." The current version of that page (updated 2026-11-17) no longer contains this sentence.

### Inference
- A third party cannot publish evaluators into customers' catalogs; each customer project must contain the evaluator (registered by the customer's identity, or by an automation the customer runs / grants access to). Ship a registration script/CLI/package that calls `create_version` idempotently per project.
- Endpoint-based evaluators are the only documented variant where our code stays on our side. Trade-off: customer data (messages, tool calls, context) is sent to our endpoint, and the customer must create a connection to it. Not a "listing", still per-project registration.
- Foundry-side versioning applies per project; our evaluator "release" needs our own version-pinning and drift check (`get_version` vs expected definition hash).

## 2. SDK and package distribution

### Documented (PyPI JSON API, accessed 2026-11-26)
| Package | Latest | Release date (PyPI upload) | Classifier | requires_python | Key deps |
|---|---|---|---|---|---|
| `azure-ai-projects` | 2.11.0 | 2026-11-13 | 5 - Production/Stable | >=3.10 | azure-core>=1.37.0, azure-identity>=1.15.0, openai>=3.0.0, azure-storage-blob>=12.15.0, isodate, typing-extensions |
| `azure-ai-evaluation` | 1.18.12 | 2026-11-12 (changelog dates it 2026-11-09) | 5 - Production/Stable | >=3.9 (classifiers list 3.9-3.11) | openai>=1.108.0, azure-identity>=1.19.0, azure-core>=1.31.0, pandas<3, nltk, httpx, Jinja2, msrest, aiohttp; extras `redteam` (pyrit==0.11.0), `opentelemetry` |

- Recent azure-ai-projects releases: 2.10.0 (2026-10-30), 2.9.0 (2026-10-17), 2.8.0 (2026-10-05), 2.7.0 (2026-09-18). Roughly biweekly cadence. `2.12.0` is "Unreleased" in the CHANGELOG. azure-ai-evaluation: 1.18.11 (2026-11-05), 1.18.10 (2026-10-23); `1.18.13` unreleased.
- Both packages are stable overall, but individual features inside them are preview (`.beta.*`, `Foundry-Features` header).
- azure-ai-projects preview opt-in: 2.0.0 (2026-03-06) introduced `allow_preview=True` on `AIProjectClient` and removed the `foundry_features` argument. With preview enabled the client sends the `Foundry-Features` header. `.beta.evaluators.list_latest_versions()` was renamed `.beta.evaluators.list()` in 2.0.0. Hosted agent methods went stable in 2.3.0 (2026-07-01). Evaluator generation jobs (rubric generation) were added in 2.2.0 and moved to LRO in 2.4.0; `EndpointBasedEvaluatorDefinition` was added in 2.3.0; `schema_version` in 2.11.0.
- Cloud evaluation API shape: `AIProjectClient(endpoint, credential=DefaultAzureCredential())`, `openai_client = project_client.get_openai_client()`, `openai_client.evals.create(name, data_source_config, testing_criteria)` and `openai_client.evals.runs.create(eval_id, data_source=...)`. Testing criteria use `TestingCriterionAzureAIEvaluator(type="azure_ai_evaluator", evaluator_name="builtin.coherence"|"<custom>", initialization_parameters=..., data_mapping=...)`. Docs install `azure-ai-projects>=2.2.0` (cloud-evaluation page, ms.date 2026-10-30).
- azure-ai-evaluation extension points (PyPI README of 1.18.12): evaluators are "custom or prebuilt classes or functions"; custom evaluators can be a plain function or a callable class with `__call__(self, *, response, **kwargs)` returning a dict; `evaluate(data=..., evaluators={...}, evaluator_config=..., target=..., azure_ai_project=<project endpoint URL>, output_path=...)` runs locally and optionally logs to a Foundry project. Prompty-based custom evaluators load `.prompty` files (Learn classic page). The "evaluate-sdk" page for the new Foundry returned 404; the classic page (updated 2026-02-27) is the documented reference for local `evaluate()`.
- Packaging custom evaluators as a PyPI package: **no Microsoft doc** describes a convention for third-party evaluator packages. The Azure-Samples custom evaluator samples are folder-based. (Absence of doc, not proof of non-support.)

### Inference
- Our package can depend on `azure-ai-projects>=2.11,<3` and `azure-ai-evaluation>=1.18` and ship from PyPI or a private index. Note that Python floors differ (3.9 vs 3.10): effective floor for a combined install is 3.10.
- Pin upper bounds: both packages change preview surface between minors (2.11.0 had "Breaking changes in beta methods").

## 3. CI/CD channels

### Documented
**GitHub Action** (`microsoft/ai-agent-evals`, README + action.yml fetched 2026-11-26; Learn "How to run an evaluation in GitHub Action", ms.date 2026-11-05)
- Usage: `uses: microsoft/ai-agent-evals@v3-beta`. Tags/releases: `v1-beta` (2025-05-06), `v2-beta` (2025-05-16), `v3-beta` (2025-12-23); all flagged pre-release on GitHub. Latest commit on main 2026-05-20 ("Increase version (#70)"). Learn page carries the standard preview banner. Action is a composite action: Python 3.10, `pip install` of the repo (`requires-python>=3.10`, `azure-ai-projects>=2.0.0`).
- Inputs: `azure-ai-project-endpoint` (req), `deployment-name` (req), `data-path` (req), `agent-ids` (req, `name:version`, comma-separated, pairwise stats vs `baseline-agent-id`), `baseline-agent-id` (opt), `api-version` (opt, in action.yml but not in README table).
- Data file: `name`, `evaluators[]` (names from the project's evaluator catalog, e.g. `builtin.fluency`), `data[]`, optional `openai_graders`, `evaluator_parameters`, `data_mapping`. README lists "Custom evaluators: define your own ... using Python code or LLM-as-a-judge patterns" and a sample `dataset-custom-evaluators.json`.
- Custom evaluator handling in action.py: it looks up each evaluator with `project_client.beta.evaluators.get_version(name, version="latest")` and flags code evaluators (`type="code"`, no `builtin.` prefix) to map `item.sample.output_text` ("because of an OpenAI limitation"). So custom evaluators must pre-exist in the customer project.
- Auth: recommended `azure/login@v2` with OIDC federated credentials (`permissions: id-token: write`), identity needs Foundry User.
- Output: GitHub step summary with scores, confidence intervals and pairwise statistical comparison.
- Hosted vs prompt agents: README/Learn say "Microsoft Foundry Agents" with `agent-name:version`; action.py resolves via `project_client.agents.get_version`. No explicit hosted/prompt distinction is documented.

**Azure DevOps extension** (Marketplace `ms-azure-exp-external.microsoft-extension-ai-agent-evaluation`, "Microsoft Foundry Evaluation Azure DevOps Extension"; manifest `vss-extension.json` in the same repo, tags `[Preview]`, category Azure Pipelines, publisher `ms-azure-exp-external`, manifest version `0.0.x`; CDN asset path shows build `0.0.34197834`)
- Task `AIAgentEvaluation@2` with the same inputs as the action (`azure-ai-project-endpoint`, `deployment-name`, `data-path`, `agent-ids`). Same JSON data file and "Custom evaluators" bullet. Results shown in a build results tab. Auth: "configure authentication to access your Foundry project" (details not given on the listing).

**azd evaluations extension** (Learn "Microsoft Foundry evaluations extension overview", ms.date 2026-10-25)
- `azd extension install azure.ai.evaluations` (v1.0.0-beta.2, needs azd >= 1.33.0). Declarative `evals/azure.eval.yaml`; `azd ai eval create` publishes datasets and evaluators (rubric evaluators as versioned resources) and creates the eval; `azd ai eval run start|show --fail-on pass-rate=0.8`. The GitHub Action Learn page has a full OIDC quality-gate recipe (Foundry User at project scope; Contributor on a dedicated resource group only if using `azd up`; federated credential issuer `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`).

**General alternative:** the Learn RBAC page says the workload identity or service principal used by a pipeline needs Foundry User on the project. The Learn GitHub Action page links "Azure Login action with OpenID Connect".
- Azure Pipelines: workload identity federation is a general Azure feature; I did not fetch an Azure Pipelines-specific Foundry doc (see Unverified).

### Inference
- For a customer-facing offering, the GitHub Action/ADO task is only useful for built-in evaluators and custom evaluators already registered in the customer project. It cannot run our local Python evaluators or our own scoring logic.
- A thin wrapper (our own composite action or pipeline template that installs our PyPI package, registers evaluators idempotently, runs `evals`, gates on thresholds) is the likely distribution unit. Both `ai-agent-evals` tags are `-beta`; pin by commit SHA (Learn recommends SHA pinning).

## 4. Distribution requirements per scoring path

### (a) Foundry-managed cloud evaluation with custom evaluators

#### Documented
- Evaluator definitions must exist in the **customer's** Foundry project before an eval run references them (name lookup in that project's catalog).
- Code evaluator: `grade(sample: dict, item: dict) -> float` (0.0-1.0), submitted as `code_text`; `data_schema`, `init_parameters` (`deployment_name`, `pass_threshold` required even though no LLM is called), `metrics` (continuous 0-1). Limits: <256 KB, 2 min per call, no network, 2 GB memory, 1 GB disk, 2 cores. Packages: numpy 2.2.4, scipy 1.15.2, pandas 2.2.3, scikit-learn 1.6.1, rapidfuzz 3.10.1, sympy 1.13.3, jsonschema 4.23.0, pydantic 2.10.6, deepdiff 8.4.2, nltk 3.9.1 (+corpora punkt, stopwords, wordnet, omw-1.4, names), rouge-score 0.1.2, pyyaml 6.0.2. Exception or timeout gives score 0.0 plus an error mark. Turn-level only (skipped for conversation-level runs). Agent target output is read via `item.get("sample", {}).get("output_text")` (flagged "subject to change"). Docs steer new custom code to endpoint-based evaluators.
- "Socket imports rejected" (prior finding) is not stated in the pages I fetched; only "no network access" is.
- Prompt evaluator: `prompt_text` with `{{var}}` templates, JSON output `{result, reason}`, ordinal/continuous/binary, required `deployment_name` and `threshold` init params.
- Endpoint evaluator: our HTTPS endpoint (POST per item, 120 s timeout, schema_version 0.0.2 normalized `messages`, strict response schema), customer-side connection (API key or Entra ID) and `azure-mgmt-cognitiveservices` to create it.
- Header on creation: `Foundry-Features: Evaluations=V1Preview` (REST and SDK `headers=`). Preview terms apply ("no SLA, not recommended for production").
- Run: `openai_client.evals.create` + `evals.runs.create`. Agent target: `{"type": "azure_ai_agent", "name": ..., "version": ...}` (version optional, latest if omitted), data source `azure_ai_target_completions`. Works for prompt and hosted agents. Hosted agents on the invocations protocol need a different `input_messages` format. Results stored in the customer project, viewable in the portal, optionally routed to App Insights.
- RBAC: Foundry User (formerly Azure AI User) on the Foundry project for running evals (portal/SDK/CI). Add Foundry User at the Foundry account scope if the eval invokes a model deployment. Extra roles only for trace evaluations (Reader on App Insights for the project managed identity, etc.), BYO storage (Storage Blob Data Contributor to the project managed identity) and scheduled/continuous rules.
- Region limits: some evaluation features are regionally restricted (cloud-evaluation page).

#### Inference
- Shipping unit = a registration bundle (evaluator definitions as JSON/Python) plus a script executed with a customer identity that can create evaluators in their project. The preview header is mandatory.
- Code evaluators suit our deterministic checks only if they fit the package list and need no network or conversation-level input. Anything needing other packages, external calls or whole-conversation scoring must use an endpoint evaluator (which we host).

### (b) Customer-process / customer-code scoring through the SDK

#### Documented
- `azure-ai-evaluation` `evaluate()` runs locally in the customer's process with callable or class evaluators, optional `target`, `azure_ai_project` for result logging, `output_path` for JSON.
- Install via `pip` from PyPI (Python >=3.9 for azure-ai-evaluation, >=3.10 for azure-ai-projects). Auth: `DefaultAzureCredential` (supports OIDC federated credentials via azure/login or workload identity in CI). Judge model needs a deployment in the project (`AZURE_AI_MODEL_DEPLOYMENT_NAME`) or an Azure OpenAI config.
- Customer-process scoring against agents: the SDK can invoke prompt and hosted agents through the project's OpenAI client/responses API (azure-ai-projects README: `.get_openai_client()` for Responses/Conversations/Evaluations; `.agents` includes Hosted Agents).

#### Inference
- Our package from PyPI or a private index (Azure Artifacts, etc.) needs pip access from the CI runner. Supply constraints (`azure-ai-projects>=2.11,<3`) and test on Python 3.10-3.13 (azure-ai-projects classifiers include up to 3.15; azure-ai-evaluation lists up to 3.11 plus pandas pins for 3.13/3.14).
- Customer-code path has no sandbox, so it is the escape hatch for logic that cannot run in a code evaluator. We can also upload results to the customer project through `azure_ai_project=`.

### Per-scoring-path requirements table

| Requirement | (a) Cloud, prompt evaluator | (a) Cloud, code evaluator | (a) Cloud, endpoint evaluator | (b) SDK in customer process |
|---|---|---|---|---|
| What ships to customer | Registered definition (prompt text) | Registered definition (Python `grade` source <256 KB) | Registered definition + project connection; we host endpoint | PyPI/private-feed package |
| Status | Preview | Preview (Microsoft: "viable for existing/constrained") | Preview (recommended for new custom code) | Stable packages; some APIs beta |
| Header | `Foundry-Features: Evaluations=V1Preview` | same | same | Only when calling the preview evaluator APIs (`allow_preview=True` or header) |
| Runtime constraints | Judge deployment needed | No network, fixed packages, 2 min, 2 GB, turn-level | 120 s, we operate it, we receive customer data | Customer's Python env |
| Prompt agents | Supported via `azure_ai_agent` target | same | same | Via SDK client |
| Hosted agents | Supported (responses protocol same; invocations protocol different `input_messages`) | same | same | Via SDK client (hosted agents stable since 2.3.0) |
| Permissions | Foundry User on project (+ account scope for model calls). Evaluator-creation role not explicitly documented | same | same + permission to create account connection (`azure-mgmt-cognitiveservices`) | Foundry User on project + whatever the customer env needs |
| CI/CD | `ai-agent-evals@v3-beta` / ADO task / `azd ai eval` if evaluator pre-registered | same | same | Any job with SDK + OIDC |

## Contradictions

1. **Evaluator SDK namespace/opt-in:** Learn pages and the sample `sample_eval_catalog_code_based_evaluators.py` call `project_client.evaluators.create_version(..., headers={"Foundry-Features": "Evaluations=V1Preview"})` (stable client namespace, manual header). The azure-ai-projects CHANGELOG (2.0.0) says `allow_preview=True` plus `.beta.*` sub-clients and removed `foundry_features`, and `ai-agent-evals` uses `project_client.beta.evaluators.get_version`. Unresolved which is canonical; both are in current material.
2. **Code-evaluator guidance:** older assumptions and the README sentence "Custom evaluators: define using Python code or LLM-as-a-judge" imply code/prompt are the primary custom types. The 2026-11-11 Learn pages now rank Rubric (recommended) > Endpoint/prompt (preview) and call code evaluators viable only for existing or constrained scenarios.
3. **Evaluator scope:** the July 2026 snapshot says evaluators follow project scope/RBAC; the current Learn pages are silent on scope. Not contradictory, but the scope claim is unconfirmed now.
4. **Release dates:** azure-ai-evaluation 1.18.12 dated 2026-11-09 in CHANGELOG vs 2026-11-12 PyPI upload. (Prior project finding of "1.18.12 stable" is confirmed.)
5. **azure-ai-evaluation Python support:** `requires_python >=3.9` and the README says 3.9+, while classifiers list 3.9-3.11 only and deps include pins for 3.13/3.14.
6. **Prior finding "socket imports rejected":** not found in the docs I fetched (docs say only "no network access"). Possibly observed behavior from the prototype; unverified against docs.
7. **GitHub Action README vs action.yml:** README input table omits `api-version`, present in action.yml. Minor.

## Unverified / open

- No documented mechanism (or explicit statement of absence) for cross-project/hub/registry sharing, Azure ML registry publishing, Marketplace or partner listing of Foundry evaluators. Searches found nothing; confirm with Microsoft contact or API-reference scan of "evaluators" REST operations (https://ai.azure.com/api-reference/evaluators/ not fetched).
- Exact RBAC action/role needed to **create** evaluator versions (docs only say Foundry User for running; the azd page says "permission to access that project and create evaluation resources"). Not confirmed in the RBAC page; the prototype succeeded with an Entra token.
- Whether `ai-agent-evals` supports hosted agents (not documented; behavior of `agents.get_version` plus the responses target unknown).
- Whether the `ai-agent-evals` action and ADO task can use custom evaluators beyond those pre-registered (docs say "Custom evaluators" but samples only reference the catalog). I did not open `dataset-custom-evaluators.json`.
- Azure DevOps authentication detail for the extension (listing says only "configure authentication") and an Azure Pipelines workload identity doc were not fetched.
- Rubric evaluator preview/GA status per mode (page text partially retrieved: Safety and Task-level are preview; Quality status not confirmed). Rubric evaluators need an LLM and are not in scope for code distribution.
- Evaluation regional/quotas pages (`evaluation-regions-limits-virtual-network`) not fetched.
- Learn page for `evaluate-sdk` (new Foundry) 404; relied on classic page and PyPI README for local `evaluate()`.
- azure-ai-evaluation CHANGELOG path is `sdk/evaluation/azure-ai-evaluation` (not `sdk/ai/`); only the head was read.

## Source ledger

| URL | What | Version / date | Accessed |
|---|---|---|---|
| https://pypi.org/pypi/azure-ai-projects/json | Latest version, deps, classifiers, release dates | 2.11.0 (2026-11-13) | 2026-11-26 |
| https://pypi.org/pypi/azure-ai-evaluation/json | Latest version, deps, README (evaluate(), custom evaluators) | 1.18.12 (2026-11-12 upload) | 2026-11-26 |
| https://raw.githubusercontent.com/Azure/azure-sdk-for-python/main/sdk/ai/azure-ai-projects/CHANGELOG.md | Release notes, allow_preview, evaluator APIs | 2.11.0; 2.12.0 unreleased | 2026-11-26 |
| https://raw.githubusercontent.com/Azure/azure-sdk-for-python/main/sdk/evaluation/azure-ai-evaluation/CHANGELOG.md | Release notes | 1.18.12 (2026-11-09) | 2026-11-26 |
| https://raw.githubusercontent.com/Azure/azure-sdk-for-python/main/sdk/ai/azure-ai-projects/samples/evaluations/sample_eval_catalog_code_based_evaluators.py | Header and `create_version` usage | main @ 2026-11-26 | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/custom-evaluators | Custom evaluator decision guide | ms.date 2026-11-11 | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/other-custom-evaluators | Prompt/code evaluators, sandbox limits, packages | ms.date 2026-11-11 (azure-ai-projects>=2.8.0) | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/endpoint-based-evaluators | Endpoint evaluators, schema 0.0.2, connections | ms.date 2026-11-11 (azure-ai-projects>=2.11.0) | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/rubric-evaluators | Rubric evaluator (partial text only) | current | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation | Client setup, workflow, prerequisites | ms.date 2026-10-30 (azure-ai-projects>=2.2.0) | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-targets | Model/prompt/hosted agent targets | ms.date 2026-09-17 | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/evaluation-permissions | RBAC for evaluation workflows | ms.date 2026-10-30 | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry | Foundry roles (grep for evaluation: no evaluator-specific role found) | current | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry/how-to/evaluation-github-action | GitHub Action docs, azd quality gate, OIDC | ms.date 2026-11-05 | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/developer/azure-developer-cli/extensions/foundry-evaluations-extension | azd evaluations extension | 1.0.0-beta.2, ms.date 2026-10-25 | 2026-11-26 |
| https://github.com/microsoft/ai-agent-evals (README.md, action.yml, action.py, pyproject.toml, vss-extension.json; releases/tags via api.github.com) | Action/extension source | tags v1/v2/v3-beta; v3-beta release 2025-12-23; main 2026-05-20 | 2026-11-26 |
| https://marketplace.visualstudio.com/items?itemName=ms-azure-exp-external.microsoft-extension-ai-agent-evaluation | ADO extension listing | build 0.0.34197834; task `AIAgentEvaluation@2` | 2026-11-26 |
| https://learn.microsoft.com/en-us/azure/foundry-classic/how-to/develop/evaluate-sdk (search snippet) | Classic local `evaluate()` reference | updated 2026-02-27 | 2026-11-26 (snippet only) |
| https://learn.microsoft.com/en-us/%20%20azure/foundry/how-to/develop/cloud-evaluation (search snippet) | Older cloud-evaluation page: "same project-scope RBAC" | last_updated 2026-07-21 | 2026-11-26 (snippet only) |
