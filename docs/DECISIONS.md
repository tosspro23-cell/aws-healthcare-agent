# Design Decision Log

Lightweight ADR-style log, one entry per non-obvious decision, added as the
AWS build-out progresses (see [`AWS_ROADMAP.md`](AWS_ROADMAP.md) for phase
status). Newest entries at the top. The point of keeping this is to have a
concrete artifact to compare against the equivalent decisions made on the
other cloud, not just a mental note of "why we did it this way."

---

## 2026-09-07 — Stage B (pgvector on Aurora) stopped deliberately at a real account-level wall, not completed

**Context**: Stage A (local Chroma retrieval) shipped; Stage B was
planned as a one-time, evidence-collecting pgvector-on-Aurora-
Serverless-v2 experiment -- build it, load Stage A's committed
embeddings, run comparison queries, write up evidence, tear it down. A
`VectorStack` (CDK, isolated-subnet VPC + Aurora Serverless v2 +
`enable_data_api`) was designed and planned first, mirroring this
project's existing IAM/opt-in-stack patterns closely, with the same
live-verification discipline as every other phase (confirmed via Python
introspection against the actual installed `aws-cdk-lib` and a live
`aws cloudformation describe-type` call that `enable_data_api` and
`serverless_v2_*` scaling coexist on one real `AWS::RDS::DBCluster`
resource). None of that verification was wrong -- the actual blocker
was one layer further down, in this specific AWS account, and only
showed up on a real deploy attempt.

**First real wall: this account requires Express Configuration, which
CloudFormation cannot express.** The first live `cdk deploy` failed:
`CREATE_FAILED ... "To use Aurora clusters with free plan accounts you
need to set WithExpressConfiguration. To remove all limitations,
upgrade your account plan."` -- AWS's own account classification, not
an assumption. Confirmed directly that `AWS::RDS::DBCluster`'s full
~60-property CloudFormation schema has no such property at all (`aws
cloudformation describe-type --type-name AWS::RDS::DBCluster`); `aws
rds create-db-cluster help` confirms `--with-express-configuration` is
an RDS-API/CLI-only parameter. No CDK/CFN construct -- this project's
or anyone else's -- can satisfy this account's requirement, so the
whole approach pivoted from CDK to a plain `boto3` script
(`infra/scripts/create_pgvector_cluster.py`).

**Second wall, found immediately after: Express Configuration on this
account is VPC-less.** A minimal probe (`WithExpressConfiguration=True`,
no other parameters) came back with `VPCNetworkingEnabled: False` and
`InternetAccessGatewayEnabled: True` -- passing an explicit custom VPC
subnet group/security group alongside the express flag failed live:
`InvalidParameterCombination: Amazon RDS can't associate a VPC because
Internet Access Gateway is enabled.` The planned `VectorStack` VPC
(already deployed once, cleanly torn down again) turned out to be
entirely unnecessary on this account -- not an error in its own design
(it synthesized correctly, had zero NAT/IGW/EIP cost, and would have
worked fine on a standard account), just inapplicable here.

**Four more real, incremental parameter constraints, found by trying
each one and fixing it, not by reading documentation up front**:
`EngineVersion` can't be specified alongside `WithExpressConfiguration`
(Express picks its own -- 17.7, still far above any documented pgvector
minimum); `DatabaseName` can't be set at create time (create it after,
via SQL); `ManageMasterUserPassword` can't be set at create time either
(apply it via a follow-up `modify_db_cluster`, which has no such
restriction). Each fix was verified by actually retrying the live call,
not assumed from the error message alone -- `infra/scripts/
create_pgvector_cluster.py`'s own docstring and inline comments carry
the exact error text for each, in the order they were actually hit.

**The wall that actually stopped Stage B: RDS Data API does not work on
this account's Express Configuration clusters at all.** Once the
cluster was up, `modify_db_cluster(EnableHttpEndpoint=True)` reported
success and the cluster returned to `available` -- but a direct
`rds-data.execute_statement` call against the real cluster/secret ARNs
failed with `HttpEndpointNotEnabledException`, reproduced after a full
instance reboot (ruling out a timing issue) and confirmed a second time
on a second, from-scratch cluster (ruling out a one-off fluke on the
first). This account's VPC-less, Internet-Access-Gateway clusters carry
a real, public `*.rds.amazonaws.com` endpoint with
`IAMDatabaseAuthenticationEnabled: true` -- the actually-supported
connectivity path is standard PostgreSQL wire protocol against that
public endpoint, authenticated via `rds.generate_db_auth_token()` or the
managed master password, not Data API's HTTP/JSON interface. `create_
pgvector_cluster.py` proves this fresh on every run (a real `SELECT 1;`
Data API call, not an assertion resting on an earlier finding) rather
than just trusting the `HttpEndpointEnabled` flag, which reports `True`
looking fine while the actual capability is absent.

**Decision: stop here, deliberately, rather than switch to a public
endpoint + IAM auth to keep going.** That path is real and would work
(install a Postgres driver, generate IAM auth tokens, connect over the
internet) -- but it's a materially different, and materially less
isolated, architecture than this project holds itself to everywhere
else: every other AWS resource in this project that isn't meant to be
public sits behind Cognito auth, API Gateway, or a VPC boundary with no
inbound rules; this would be the first resource in the whole project
with a credential-gated *public* network endpoint and no network-layer
isolation at all. `VectorStack`'s VPC-isolated design (no NAT, no IGW,
security group with zero inbound rules, reachable only via AWS's own
internal Data API path) was a real defense-in-depth choice, not
boilerplate -- switching to a public endpoint purely to finish a
one-time learning exercise would trade that isolation property away for
no benefit the project actually needs. Express Configuration's
VPC-less/public-endpoint model is a genuine, reasonable AWS feature --
explicitly framed as a quick-start/prototyping convenience (`aws rds
create-db-cluster help`: "creates ... in seconds"), not the production
pattern most compliance frameworks and standard architectures require a
database to follow. A standard (non-free-tier) account would have hit
none of this -- the original `VectorStack` CDK design would have
deployed and worked exactly as planned.

**What ships from Stage B**: `infra/scripts/create_pgvector_cluster.py`
and `infra/scripts/destroy_pgvector_cluster.py` -- both real, live-run,
working code (create a real cluster, confirm the exact Data API
boundary with a live call, tear down cleanly with a live post-teardown
`describe_db_clusters` confirmation) -- kept as the actual, honest
deliverable of this stage: a precise, evidence-backed account
constraint, not a working three-way retrieval comparison. The
originally-planned `infra/stacks/vector_stack.py`, `infra/vector_app.py`,
`infra/scripts/load_pgvector_index.py`, `infra/scripts/query_pgvector_
evidence.py`, and `scripts/build_phase5_evidence.py` were written,
lint/mypy-clean, and (for the CDK stack) live-deployed and destroyed
once to confirm the VPC-less finding -- but removed rather than kept as
unverified code once Data API was confirmed unusable, since none of
them were ever actually exercised against a working Data API path. This
project's own standing discipline is to never claim untested code
works; keeping them around with a caveat would have been exactly that.

---

## 2026-09-07 — Fixed the omega-3 false positive in safety.py, checked for what the fix could newly hide

**Context**: follow-up to the previous entry's finding, once the user
explicitly asked for the `_NUMBER_RE` fix and to specifically look for
counterexamples the fix itself might introduce -- not just "does this
fix the one case observed live."

**Decision**: `_NUMBER_RE` gained a second negative lookbehind,
`(?<![A-Za-z]-)`, alongside the original `(?<![\w.])` -- excludes a
match starting immediately after a *letter* followed by a hyphen
(`omega-3`, `COVID-19`, `type-2`, `stage-4`), while deliberately leaving
a match starting after a *digit* followed by a hyphen untouched, so a
genuine numeric range ("5-10 servings") still flags both numbers exactly
as before -- verified directly against 14 hand-picked cases (compounds,
ranges, negative numbers in prose and parens, unit-glued values,
internal identifiers) before touching the real file, not just the one
case that failed live.

**The counterexample search the user asked for did find one real, if
narrow, residual gap**: the same lookbehind that stops "omega-3" from
being misread also hides a number *deliberately* hyphen-glued to a
preceding word from this one check -- `verify_numeric_grounding("Try
medication-500mg for this.", [])` now passes when it previously
wouldn't have. Judged acceptable and left as-is rather than chased
further: no real narrator output (mock or any of the five LLM backends,
across everything run so far) has ever produced this phrasing --
natural dosing language reads "take 500 mg", which `check_no_dosing`'s
own independent pattern list already catches regardless of this fix --
and closing this one narrow, contrived construction without a new false
positive on a real compound word would need a much more complex rule for
a benefit that's speculative, not observed. Recorded explicitly as a
known gap (a dedicated test asserts it, docstring updated, this entry
exists) rather than silently introduced and left for someone else to
discover later -- the same "honest about limits, not a claim of
completeness" standard `safety.py`'s own module docstring already holds
itself to.

**Verification**: `tests/test_safety.py` gained three tests -- the fix
itself (four real compound-word phrasings, all now pass), the regression
guard (the hyphenated range case, still correctly fails on both numbers),
and the known-gap test (asserts the hyphen-glued dose passes, documenting
rather than hiding it). Full suite unaffected (204 passed, unchanged
coverage). Re-ran the exact live experiment from the previous entry
end-to-end against real Bedrock, not just the unit tests: 8/8 real runs
of "What foods should I eat to lower my LDL cholesterol?" (Chroma
retriever) returned `narrator_backend=bedrock` with zero fallbacks,
against the previous entry's measured 3/6 fallback rate before this fix
-- the same live scenario that exposed the bug, now confirmed fixed
live, not just believed fixed from the regex change alone.

---

## 2026-09-07 — Closed the retrieval-to-generation gap for LLM narrators; found a real false-positive it exposed in safety.py

**Context**: The previous entry's honest finding was that retrieval drove
*citations*, not *generation content*, for every narrator -- deliberately
correct for `MockNarrator` (its whole safety argument is "template over
already-verified structured facts only"), but a real, closable gap for
the LLM narrators, which only ever saw citation *names*, never the
educational text those citations represent.

**Decision**: added `narrator/_prompt.py::build_user_message()`, a single
shared function replacing five near-identical, duplicated f-strings (one
per narrator -- Bedrock/Anthropic/OpenAI/Google/Ollama all built the
exact same "User's question... Grounded facts..." string inline). When
`brief.retrieved_chunks` is non-empty, it now appends a clearly labeled
"Reference material" section -- the top `MAX_REFERENCE_CHUNKS` (3)
chunks' own `content`, explicitly instructed as "general educational
background... not a new grounded fact... do not restate any specific
number from here unless it already appears in the grounded facts above."
`SYSTEM_PROMPT` gained the same instruction. With no retrieved chunks
(every existing narrator test's `Brief()`), the function produces
byte-identical output to the old inline string -- confirmed by a
dedicated test (`tests/test_prompt.py`) before touching any narrator,
and all five pre-existing narrator test suites (bedrock/openai/google
already in CI's optional-extra tier; ollama already unconditional; a
from-scratch manual smoke test for `AnthropicNarrator`, which has no
test file at all -- a pre-existing gap, not introduced here) still pass
unmodified. `safety.py` was not touched -- the instruction is a
courtesy, same as always; the real guarantee stays `run_safety_checks`
re-verifying whatever text comes back regardless of what fed the prompt.

**A real false positive this surfaced, found by actually running it
against live Bedrock, not assumed safe**: asking "What foods should I
eat to lower my LDL cholesterol?" with the Chroma retriever (whose top
chunks are genuinely about food/LDL, so the model now has real
reference material to draw on) produced a **50% fallback rate** over 6
real runs (3/6), every one flagged as `numeric_grounding (ungrounded
numbers: ['3'])`. The actual cause, found by reading the full rejected
draft and reproducing it directly against `safety.py`'s own regex: the
model naturally writes "omega-3 fatty acids" once nudged toward
fish/nutrition content, and `_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+
\.?\d*")`'s negative lookbehind only excludes a preceding word character
or period -- a hyphen isn't either, so `omega-3` extracts a bare `'3'`
as if it were a standalone ungrounded numeric claim:
```
>>> _NUMBER_RE.finditer("omega-3 fatty acids")
['3']  # at the position right after the hyphen
```
This is a **pre-existing gap in `_NUMBER_RE`, not something this change
introduced** -- the regex would have mis-parsed "omega-3" in any answer
that happened to contain it before today, LLM-narrator or not. What
this change did was make the model far more likely to *actually write*
"omega-3" (real, relevant reference content about fish now sits in its
prompt), turning a latent, rarely-triggered parsing gap into a
measurable, repeated fallback for one specific, entirely safe class of
answer. The *safety* outcome is correct either way -- fallback is what
this net is supposed to do when it can't confirm groundedness, and
`safe=True` held on every single run, fallback or not -- the cost is
purely a quality/UX one: a real, harmless, useful answer gets discarded
for the plainer template answer more often than it should.

**Deliberately not fixed as part of this change**: the user was explicit
that the safety architecture itself should stay untouched while doing
this work, so `_NUMBER_RE` was left exactly as it was rather than
patched unilaterally -- even though a narrow fix (excluding a digit
immediately after a hyphen that's itself preceded by a letter, i.e. a
compound term like "omega-3"/"top-10"/"covid-19" rather than a genuine
negative number) looks like a real, scoped, *precision* improvement to
the existing check's own stated intent (catch standalone ungrounded
numbers) rather than a loosening of what counts as safe. Recorded here
as a concrete, evidence-backed follow-up candidate rather than an
unexamined assumption that "the safety net just handles it" -- it does,
correctly, but at a real, now-measured cost.

---

## 2026-09-07 — Local vector retrieval (Chroma), Stage A of a two-stage learning exercise

**Context**: A deliberate, explicitly-optional Phase 5 item (see
`AWS_ROADMAP.md`) -- BM25 genuinely isn't insufficient at this 68-chunk
corpus size, this is a hands-on-learning + portfolio addition, done as
two independent stages so the larger, riskier cloud stage (a one-time
pgvector-on-Aurora-Serverless-v2 experiment, not yet started) can't block
or complicate the smaller, safer local one.

**Decision, the interface**: `retrieval.py` became a `retrieval/` package,
mirroring `narrator/`'s existing swappable-backend shape exactly, for the
same reason narrator has several backends -- retrieval just gained its
second real one. `retrieval/base.py` defines `Retriever` as a
`typing.Protocol` (matching `Narrator`, not an ABC); `agent.py` gained
`_select_retriever()` (mirroring `_select_narrator()`'s env-var-driven,
lazy-import-per-branch shape) and a `retriever=None` constructor
parameter on `HealthAgent` -- a real gap this closed, since retrieval
previously had no injection seam at all (`self.retriever =
KnowledgeRetriever(kb_path=kb_path)` was hardcoded). `AgentTrace` gained
a `retriever_backend` field alongside the existing `narrator_backend`,
for the same reason: visible in `--trace` output which backend actually
answered.

**Decision, Chroma over FAISS**: the user has hands-on experience with
both. Chroma was chosen specifically because it's pure Python + SQLite
persistence, which fits this project's existing Lambda packaging model
(`infra/build_lambda_asset.py` does a plain `shutil.copytree`, no `pip
install`/Docker bundling step) far better than FAISS's compiled
`hnswlib`-backed wheel would. **This turned out to be only half the
packaging problem**: `chromadb` itself still pulls in compiled
dependencies (also `hnswlib`, among others) via a platform-specific
wheel, so even Chroma can't be added to the deployed Lambda's flat-copy
asset without a real `pip install --platform ...`-style step -- new
packaging work, not attempted here. `ChromaRetriever` is therefore kept
**local/CLI-only for now**, deliberately, at the same status this
project's `anthropic`/`openai`/`google` narrators already have: fully
implemented, fully tested, never wired into any deployed Lambda's
environment. Extending `build_lambda_asset.py` to actually bundle it is
documented future work, not silently dropped.

**Decision, embeddings**: never bundle a heavy embedding library
(`sentence-transformers`/`torch` would be multi-hundred-MB, infeasible
for the flat-copy model regardless of Chroma's own packaging). Instead:
`scripts/build_vector_index.py` makes 68 real, one-time Bedrock Titan
Embeddings calls (`amazon.titan-embed-text-v2:0`, confirmed live against
this account rather than assumed -- request shape `{"inputText": ...,
"dimensions": 512, "normalize": true}`, response has an `embedding` field
-- see that script and `chroma_retriever.py`'s own module docstring) to
precompute the corpus's embeddings once, committing both a JSONL sidecar
(`data/knowledge_base_embeddings.jsonl`, the single source of truth these
vectors -- also what a future Stage B would reuse rather than
re-embedding) and the resulting Chroma index directory
(`data/vector_index/`) to git, mirroring the already-committed, generated
`data/mock_biomarker_catalog.sqlite` as precedent for a generated binary
artifact checked in and bundled via flat copy. Only the *live query text*
is embedded at request time, via one small Bedrock call -- the same "one
paid model call the app is already allowed to make" policy every other
cloud-backed component here follows. `tests/test_vector_index_freshness.py`
is a free, always-run (no `chromadb`/`boto3` needed) regression guard
that the committed sidecar's id set and embedding dimension still match
the live KB and `EMBEDDING_DIMENSIONS` constant.

**A real, if minor, correctness fix found while building this**: Chroma's
raw default distance space is squared L2, not cosine -- `1.0 - distance`
against that default produced negative, unbounded "similarity" values
(confirmed by an actual smoke-test run, not assumed). Fixed by explicitly
setting `metadata={"hnsw:space": "cosine"}` on collection creation, after
which `1.0 - distance` is a real cosine similarity in `[-1, 1]`. Ranking
*order* was identical either way (squared L2 over normalized vectors is a
monotonic transform of cosine distance), so this was a score-interpretability
fix, not a retrieval-quality bug -- caught before committing the index by
comparing raw output against the expected range, the same "verify the
actual behavior, don't assume" discipline this project applies everywhere
else.

**Decision, the read-only-filesystem copy**: `ChromaRetriever` never opens
a `PersistentClient` directly against the committed `data/vector_index/`
-- it copies it to a fresh `tempfile.mkdtemp()` location first (cached
per source path so repeated construction in one process doesn't recopy),
unconditionally, not just as a Lambda-specific `/var/task`-is-read-only
workaround. Reasoning: never mutate a committed, version-controlled
directory as a side effect of opening it for reads, locally or deployed --
Chroma's persistent client can attempt to write lock/WAL files on open
even for read-mostly access.

**Verification**: `tests/test_chroma_retriever.py` (skipped via
`pytest.importorskip("chromadb")` when the optional extra isn't
installed -- CI's default `pip install -e ".[dev]"` never installs it,
confirmed by running the exact CI install in a fresh venv and checking
the skip is clean, not an error) builds a small fully-synthetic index
per test (hand-picked orthogonal unit vectors, never the real 68-chunk
corpus) with an injected fake `embed_fn`, so ranking/`top_k`/topic-filter-
boost/`get_by_id` assertions are deterministic without any real Bedrock
call -- mirroring `tests/test_bedrock_narrator.py`'s exact two-layer
pattern (fake business logic + `unittest.mock.patch("boto3.client")` for
the default wiring). Full suite (`pytest -q --cov`, `ruff check`, `ruff
format --check`, `mypy src`) reconfirmed both with the `chroma` extra
installed (91% coverage, 100% on the new module) and, separately, in a
from-scratch venv matching CI's exact install (no `chroma`/`bedrock`
extras) to directly confirm the skip path and that `mypy` doesn't choke
on `chromadb`'s transitively-installed `numpy` stubs when `chromadb`
itself is absent. Real end-to-end CLI runs (`CARE_AGENT_RETRIEVER_
BACKEND=chroma python -m care_agent ask ... --trace`) against the real
committed index, real live Bedrock query embedding, confirm real,
sensible results and the new `retriever_backend` trace field.

**Retrieval-quality comparison, run for real** (`scripts/
compare_retrievers.py`, output: `docs/RETRIEVAL_COMPARISON.md`): both
backends queried directly (no `topic_filter`, isolating pure
text-to-relevance matching from the full agent pipeline's own
topic-tag construction) against 8 differently-styled real questions.
Average top-5 overlap across all 8: 2.0/5 -- not identical, but not
random either. The actual pattern, not assumed going in: **when a
query shares vocabulary with the target content** (technical/exact
phrasing -- "high hs-CRP inflammation marker", "vitamin D low what does
that mean", "I have knee pain, how should I exercise?"), both backends
converge heavily, often agreeing on the exact same top-2/top-3 ranked
results. **When a query paraphrases rather than matching KB vocabulary
directly** -- "What foods should I eat to lower my LDL cholesterol?"
never says "nutrition" or "DASH" -- Chroma's semantic match stays
on-topic (4 of its top 5 are genuinely about food/LDL) while BM25's
tag-boost-free lexical score surfaces less relevant chunks. One concrete
BM25 failure mode this surfaced: `kb_eval_003` ("Presentation quality
expectations" -- a meta chunk about how this project should be reviewed
in an interview, entirely unrelated to health content) appeared in
BM25's top 5 for three unrelated real questions (LDL foods, knee pain,
vitamin D), purely from generic word overlap ("should", "explain",
"how") scoring well enough on IDF -- Chroma never surfaced it once. This
is the honest headline finding, not overstated: neither backend is
strictly better at this corpus size, and the project's own default
(BM25) remains the right choice for `topic_filter`-heavy production
queries (see `agent.py`'s own tag construction) -- but the comparison
is a real, concrete illustration of lexical vs. semantic retrieval's
actual tradeoff, not just an assertion of one.

**Follow-up question, and a real architectural gap it surfaced: does a
better-retrieved chunk actually produce a better final answer?** Checked
directly rather than assumed. With the default `MockNarrator`: no
measurable effect at all -- `HealthAgent.ask()` run against the same 4
questions under both backends returned byte-for-byte identical answer
text except for the trailing `Sources:` line. Reading `mock_narrator.py`
confirms why: `_sources_line()` is the *only* place `brief.retrieved_
chunks` is read anywhere in that file (one `grep` match), and it only
extracts `chunk.source_name`/`chunk.source_url` for the citation footer
-- every other line of the template comes from `grounded_facts` and
`questionnaire_modifiers`, never from a retrieved chunk's own `content`
field. The knowledge-base text itself is inert for narration purposes;
retrieval only selects which citations get named.

With the real Bedrock narrator (what the deployed Workbench actually
runs), the retrieved chunks' *names* do reach the model, since
`BedrockNarrator.compose()` sends `MockNarrator`'s full output --
including that same `Sources:` line -- as the "grounded text" to
rephrase. A first real run on "What foods should I eat to lower my LDL
cholesterol?" looked like a clean win for Chroma: its sources (NHLBI's
"DASH Eating Plan", AHA's "lower-your-ldl") produced a specific,
useful food list, while BM25's sources (a CRP MedlinePlus link, a
"metabolic-priority" mock policy) produced a much more hedged "I can't
recommend specific foods" response. **A second repeat of the exact same
comparison undercut that conclusion**: BM25 this time also produced a
specific food list (oats, barley, fatty fish, nuts), just as detailed as
Chroma's. Bedrock's `converse` call isn't made at a fixed
temperature/seed, so run-to-run generation variance for the *same*
backend was at least as large as the difference between backends --
two runs each is not enough evidence to attribute the first run's
apparent difference to retrieval quality rather than sampling noise.

**The honest conclusion, and what it says about the architecture, not
just this experiment**: retrieval's actual reach into the final
user-visible answer is currently narrow by design, not by accident. The
mock path's entire safety argument rests on never emitting anything that
wasn't already independently verified by `reasoning.py` (`grounded_
facts`, structured `questionnaire_modifiers`) -- deliberately excluding
free-text KB content from that template is the *correct* choice there,
not a gap, since introducing unstructured prose into a path whose whole
value proposition is "inspectable, deterministic, nothing unverified"
would weaken exactly what makes it safe. The LLM path is different: it's
already meant to synthesize free text, and today it only ever sees
*citation names*, never the actual educational content those citations
represent -- meaning its specific advice is drawn from the model's own
training knowledge, not from this project's curated knowledge base, even
though a knowledge base exists and was retrieved. That's the real,
closable gap: extending `BedrockNarrator.compose()` (and the other LLM
narrators) to include a short excerpt of each retrieved chunk's own
`content` in the prompt -- clearly labeled as reference material, not
new grounded fact -- would make the LLM's specific suggestions
genuinely traceable to the vetted KB rather than incidental to the
model's general knowledge, without touching `safety.py`'s existing
post-hoc verification at all (the final text is still independently
re-checked regardless of what fed the prompt, so the safety net doesn't
need to change to add this). Not implemented as part of this stage --
recorded here as a genuine, scoped follow-up rather than left as an
unexamined assumption that retrieval quality obviously mattered
end-to-end.

---

## 2026-09-06 — Deploy job skips its own approval gate for docs-only pushes

**Context**: The eval-trend-chart push (previous entry) required the
same manual production approval as every other push to `main`, despite
changing a chart and some documentation. The reasonable-sounding
objection to that -- "this didn't touch anything, why does it need a
human to approve a deploy" -- turned out to be only half right on
inspection: `infra/build_lambda_asset.py` copies the *entire*
`src/care_agent/` directory into the Lambda deployment package
wholesale (see its own docstring), so the new `eval_trend.py` module
did change the deployed code's content hash, even though no Lambda
handler imports it. `cdk deploy` would have found a real (if
practically inert) Lambda code update, not a no-op. But `docs/`,
`README.md`, and `scripts/` changes genuinely don't touch anything any
stack or asset reads -- forcing a human through the same approval
prompt for those has no corresponding safety benefit, just friction on
every small edit.

**Decision**: a new `check-deploy-paths` job runs independently
alongside `test`/`smoke`/`infra`/`frontend` (pure `git diff`, no
dependency on the others, so it doesn't lengthen the critical path) and
diffs `github.event.before` against `github.sha` -- the exact commit
range this push introduced, not the whole PR/branch history. `deploy`'s
`if` now also requires `needs.check-deploy-paths.outputs.deploy_relevant
== 'true'`. The path set that counts as deploy-relevant: `infra/`,
`frontend/`, `src/care_agent/`, `data/` (also copied wholesale into the
Lambda asset), and `.github/workflows/ci.yml` itself (a change to the
deploy job's own steps is best confirmed by actually running them, not
skipped by the same job it's changing). Deliberately coarse per-directory
matching, not per-file or per-import-graph: a change that's *inside*
one of these directories but provably unreachable at runtime (like
`eval_trend.py` itself) still counts, because under-matching -- silently
skipping a deploy that should have happened -- is the one failure mode
worse than an occasional unnecessary approval prompt. A push that
creates a new branch (`github.event.before` all-zeros, nothing to diff
against) also deploys rather than guesses.

**What this doesn't change**: the approval gate itself, and the OIDC
trust scoping it's tied to (see the CD pipeline entry above), are
untouched -- this only decides *whether* the `deploy` job runs at all
for a given push, not what it's allowed to do once it does. A push that
touches even one deploy-relevant file still goes through the exact same
human-approval flow as before.

**Verification**: confirmed the filter's regex against two real commits
from this session's own history -- a docs/chart-only "chore: update eval
history" bot commit (correctly `false`) and a commit touching
`infra/stacks/cicd_stack.py` (correctly `true`) -- via `git diff
--name-only <before> <after>` run locally against the actual repo, not
a synthetic example. `python3 -c "import yaml; yaml.safe_load(...)"`
confirms the workflow file itself still parses and that `deploy`'s
`needs`/`if` reference the new job correctly. The next real push is the
end-to-end confirmation that a docs-only change skips the approval
prompt entirely -- not yet observed as of this entry.

---

## 2026-09-06 — Eval pass-rate trend chart, hand-rolled SVG instead of a charting library

**Context**: The last of three post-eval-framework asks (CD pipeline and
cdk-nag landed first, both above). `docs/EVAL_HISTORY.md` already
recorded pass rate per commit, but as a growing stack of markdown
tables -- reading a trend out of that meant scrolling and eyeballing
percentages across entries, not actually seeing one.

**Decision, a structured log alongside the prose one**: rather than
parsing the existing markdown table back into data to plot it (the same
class of mistake as this project's own ordinal-list-marker regex bug --
just applied to output instead of input, and just as fragile if the
table's format ever changes), `scripts/update_eval_history.py` now also
appends one JSON line per run to `docs/eval_history.jsonl` -- built
from the exact same `EvalSummary` the markdown entry itself is rendered
from, so the two can never drift apart. New module
`care_agent/eval_trend.py` owns the record shape
(`HistoryRecord`: date, commit, narrator backend, pass rate, counts)
and the append/read functions; `scripts/update_eval_history.py` stays a
thin orchestrator, the same split `care_agent.eval` vs.
`scripts/update_eval_history.py` already has.

**Decision, no charting library**: the chart itself
(`care_agent.eval_trend.render_svg_trend`) is four SVG primitives --
gridlines, a polyline, circles, text labels -- built as an f-string,
not matplotlib or a JS charting package. This project's own
`pyproject.toml` has an empty `dependencies` list by design; a handful
of `<line>`/`<circle>`/`<polyline>` tags didn't justify breaking that
for a static image regenerated by a script that already has no other
dependencies. The rendered SVG is embedded directly in
`EVAL_HISTORY.md`'s own header (`![...](eval_trend.svg)`), which
GitHub renders inline in the markdown view -- confirmed by opening the
committed file on github.com, not assumed. Points are colored by
narrator backend (`mock`/`bedrock`) rather than pass/fail, since the
y-position already encodes the result and the color's actual job is
distinguishing the free, CI-run path from an occasional hand-run,
real-money Bedrock check -- a legend only appears when a run of both
backends is actually present in the log, so the common case (CI-only,
one backend) stays uncluttered.

**Decision, no backfill**: the JSONL log starts empty rather than being
seeded from the six same-day entries `EVAL_HISTORY.md` already had --
doing that would mean parsing this project's own generated prose back
into structured data, the exact pattern the JSONL log exists to avoid
needing ever again. The chart is sparse until the log accumulates a
few more runs; honest about starting now beats a backfill built by
un-doing the very serialization step this design is meant to prevent.

**Verification**: `tests/test_eval_trend.py` (8 tests, 100% line
coverage on the new module) covers the empty/one-point/multi-point/
mixed-backend rendering paths and the JSONL round-trip, plus a
direct assertion that a lower pass rate places its point lower on the
chart (not just "a chart renders"). Ran `scripts/update_eval_history.py`
locally to confirm real output: a valid `docs/eval_trend.svg` and a
correctly-appended `docs/eval_history.jsonl` line, and that
`EVAL_HISTORY.md`'s regenerated header carries the image embed on an
existing file, not only a freshly-created one (the header is now always
re-rendered from `_HEADER` on every run rather than reusing whatever
header text the existing file already had, so a template change like
this one actually propagates instead of being silently frozen into
files created before it).

---

## 2026-09-06 — cdk-nag security gate, and a live bug it caught during its own rollout

**Context**: The last of three post-eval-framework asks, in priority
order: a real CD pipeline (previous entry), automated security scanning
(`cdk-nag`), and eval trend charts. Every IAM grant in this project had
already been manually reviewed across three independent-review rounds --
`AwsSolutionsChecks` turns that into a standing, automatic gate against
AWS's own Well-Architected security rules on every `cdk synth`, so a
*new* stack or resource added later doesn't quietly regress on something
this project already knows how to check for.

**A version incompatibility found before any real check could run**:
`cdk-nag` 3.0.2 (latest on PyPI at the time) fails outright against this
project's aws-cdk-lib/jsii combination -- `TypeError:
aspectApplication.aspect.visit is not a function` -- reproduced against
a minimal single-bucket stack with no other code involved, not just this
app, so it's a real library incompatibility, not a bug in this project's
usage. Pinned to `cdk-nag==2.38.2` instead, confirmed working the same
way.

**Findings, fixed** (see `stacks/` diffs for each): Cognito password
policy now requires symbols (`AwsSolutions-COG1` -- the policy already
required length/case/digits, just not symbols); DynamoDB point-in-time
recovery enabled on `RunsTable` (`DDB3`); every Lambda bumped from
Python 3.12 to 3.13 (`L1`); the Step Functions state machine now logs
`ALL` events to CloudWatch and has X-Ray tracing enabled (`SF1`/`SF2`);
the HTTP API's stage now has access logging (`APIG1`) via the same
L1-property-override mechanism the throttle fix already used on this
same auto-created stage -- the first attempt passed a jsii
`AccessLogSettingsProperty` object to `add_property_override`, which
CloudFormation rejected outright (`Additional properties are not
allowed ('destinationArn' was unexpected)`) since property overrides
don't run objects through the usual camelCase-to-PascalCase conversion
a normal constructor prop would; fixed with a plain PascalCase dict
instead.

**Findings, deliberately not fixed, tried and reverted**: raising
CloudFront's minimum TLS version (`CFR4`) turned out to be impossible
without a custom domain + ACM certificate -- confirmed directly via
`cdk synth`'s own warning (*"Ignoring 'minimumProtocolVersion': ... The
distribution uses the CloudFront default certificate, whose security
policy is fixed at TLSv1"*), not assumed. Registering a real domain
solely to raise this floor is out of scope for a demo project with no
domain of its own; suppressed instead, with that exact synth output
quoted in the suppression's own `reason` field.

**Findings, suppressed with a written, per-finding reason** (see the new
`infra/nag_suppressions.py` -- every `reason` is also visible in the
deployed stack's own template metadata, not just in source): Cognito
MFA/Plus-tier (`COG2`/`COG8` -- real ongoing cost or friction with no
benefit against this project's actual threat model: synthetic data,
`AdminCreateUser`-only accounts); S3 server access logs on both buckets
(`S1` -- would roughly double storage cost logging access to
non-sensitive demo data); CloudFront geo-restriction/WAF/access-logging
(`CFR1`/`CFR2`/`CFR3` -- no real traffic to defend, and API-level access
is already logged at `ApiStack`'s own stage); the `AWSLambdaBasicExecutionRole`
managed policy (`IAM4` -- CDK's own standard minimal-logging policy,
attached automatically); and every `IAM5` wildcard remaining after the
fixes above, each traced to its exact source and confirmed unavoidable:
an object-key-suffix wildcard on the evidence bucket (the only way to
grant "any object in this bucket"), a state-machine "any execution of
this one machine" ARN pattern, Lambda-invoke grants' CDK-standard `:*`
alias suffix, the `logs:CreateLogDelivery`/`xray:PutTraceSegments`-family
APIs that don't support resource-level scoping *at all* per AWS's own
IAM reference (confirmed by reading the actual synthesized policy, not
assumed), and CDK's own `BucketDeployment` L3 construct's internal
Lambda role (entirely AWS's own generated code, not this project's
grant). `assert_no_overly_broad_iam_policy` (this project's own,
stricter test helper, predating cdk-nag) needed a matching, narrowly
scoped exception for the `logs`/`xray` case specifically -- it's an
explicit action allow-list, not a blanket exemption, so a statement
mixing in any other action still fails.

**A real, live bug found during this work's own post-deploy smoke
test, not a hypothetical**: after redeploying every Lambda to Python
3.13, a live queue-path run against the real Bedrock backend came back
`narrator_backend: mock` instead of the expected `bedrock` --
`trace.rejected_draft` showed a perfectly good answer whose numbered
list had its ordinal markers wrapped in Markdown bold
(`"**1. See your clinician soon**"`), which `_ORDINAL_LIST_MARKER_RE`
(requiring digits at the exact start of a line) never recognized as a
list marker at all, since `**` came first -- so "1", "3", "4" looked
like fabricated bare numbers. The safety pipeline's own fallback
behavior worked exactly as designed (a safe mock answer was served
instead of a wrongly-rejected real one, the same asymmetry this
project has applied throughout), but the underlying false positive is
worth closing so real Bedrock answers stop being needlessly discarded.
Fixed by tolerating up to two leading `*`/`_` characters before the
ordinal digits; re-ran the exact rejected draft text directly against
the fixed regex to confirm it now passes, then confirmed the same live
question again post-redeploy: `narrator_backend: bedrock`, `safe: true`.

**Verification**: `cdk synth`/`cdk deploy` (the real CLI entrypoints,
not a bespoke test script) now run `AwsSolutionsChecks` automatically --
confirmed zero unsuppressed `AwsSolutions-*` findings across all 8
stacks, including `CareAgentBudgetStack` (only built when
`CARE_AGENT_BUDGET_EMAIL` is set) and `CareAgentCiCdStack`. Full infra
suite (157 tests, all passing after extending the IAM-wildcard test
helper's exception list) and full kernel suite (170 tests, up from 168,
after the ordinal-list-marker fix) both pass; `ruff`/`mypy` clean on
both. All 8 stacks redeployed live; password policy, point-in-time
recovery, Step Functions logging/tracing, and API access logging all
confirmed directly against the real deployed resources (not assumed
from the template alone). The existing `infra` CI job's `cdk synth`
step now enforces this gate on every push for free, no new CI step
needed.

---

## 2026-09-06 — CD pipeline: GitHub Actions deploys via OIDC, gated by a manual production approval

**Context**: The first of three post-eval-framework asks (cdk-nag and
eval trend charts follow). Every deploy up to this point was a human
manually running `cdk deploy` from a local session with the AWS `dev`
profile's long-lived credentials -- no audit trail independent of that
session, no gate other than "did the person doing it also happen to run
the tests first," and (demonstrated live earlier this same project,
twice) a real risk of a forgotten deploy-time env var silently breaking
something (the CORS regression from omitting
`CARE_AGENT_WORKBENCH_URL` -- see the 2026-09-05 entries above).

**Decision, identity**: a new `CiCdStack` creates a GitHub Actions OIDC
identity provider plus an IAM role GitHub can assume -- no AWS access
key/secret stored anywhere in the repo or its GitHub configuration. The
trust condition is `StringEquals` (not `StringLike`) on both
`token.actions.githubusercontent.com:aud` (`sts.amazonaws.com`) and
`:sub` (`repo:tosspro23-cell/aws-healthcare-agent:ref:refs/heads/main`)
-- an exact match, so a pull request, a fork, or any other branch can
never assume this role, confirmed directly against the deployed role's
own trust policy (`aws iam get-role`), not assumed from the CDK source
alone. The role itself carries almost no direct permission: only
`sts:AssumeRole` on the four CDK-bootstrap-generated roles
(`deploy-role`/`file-publishing-role`/`image-publishing-role`/`lookup-role`)
for this exact account/region -- the same roles a human's local
`cdk deploy` already uses, confirmed live via this account's own
`CDKToolkit` bootstrap stack (default `hnb659fds` qualifier, standard
bootstrap, no custom exec-policy restriction) before writing a single
line of this stack.

**A live deploy mistake, not a design flaw, caught before it could
actually happen**: the first attempt set `create_default_stage=False`
on `ApiStack`'s `HttpApi` and created a brand-new, explicitly-throttled
`HttpStage` construct -- reasonable in isolation, since `HttpApiProps`
has no way to pass `throttle` through to the stage that `HttpApi`
creates automatically. Deployed live, this failed outright: `Resource of type
'AWS::ApiGatewayV2::Stage' ... already exists`, because the new
construct's logical ID differs from the one the already-deployed
auto-created stage uses, and CloudFormation won't let two different
logical resources both claim the same physical stage name. Fixed with
an L1 property override on the existing stage instead (same logical
ID, so CloudFormation treats it as an in-place update) -- this predates
today's cdk-nag work but is the exact mechanism the access-logging fix
above reused.

**Decision, the pipeline itself**: a new `deploy` job in the existing
`ci.yml`, gated on every other job (`test`/`smoke`/`infra`/`frontend`)
passing first, `push`-to-`main`-only (never a PR), that assumes the
OIDC role and runs the exact same `cdk deploy --all` a human would run
locally -- `frontend/.env.local` (gitignored, and holds nothing actually
secret: a public Cognito app client ID and a public API URL, both
already embedded in the publicly-served JS bundle regardless) is
written fresh from the currently-deployed stacks' own CloudFormation
outputs immediately before the build, rather than duplicated into a
GitHub variable that could silently drift out of sync with the real
deployed values.

**Decision, the approval gate**: rather than standing up a second,
separate AWS account purely to get a "staging" environment (a real
option, but disproportionate infrastructure for a single-account demo
project), the `deploy` job references a GitHub `environment: production`
configured with a required reviewer (created via the GitHub API,
confirmed via `gh api .../environments` afterward) -- every deploy
pauses for a human's explicit approval in the GitHub UI before it runs,
regardless of how the four gating jobs came out. This gets the actual
property that matters (a human approves before production changes,
every time) without the cost/complexity of a second environment.

**Verification**: `infra/tests/test_cicd_stack.py` (5 tests) asserts the
exact trust-condition shape (`StringEquals`, not `StringLike`; the
literal expected `sub` string) and that the assumable-role list is
exactly the four bootstrap roles, never a wildcard resource -- the same
rigor `tests/iam_assertions.py` already applies elsewhere in this
project. Deployed live: `aws iam list-open-id-connect-providers` and
`aws iam get-role` confirm the exact scoping described above against
the real account, not just the synthesized template. `AWS_DEPLOY_ROLE_ARN`
and `CARE_AGENT_WORKBENCH_URL` stored as GitHub repository *variables*
(not secrets -- neither is sensitive); `CARE_AGENT_BUDGET_EMAIL` stored
as a secret, matching `BudgetStack`'s own existing opt-in-via-env-var
design.

**The approval gate worked; the OIDC trust condition itself didn't, on
the very first real run** -- exercised for real (not a synth-only
guess) the moment this stack's own introducing commit was pushed:
`test`/`smoke`/`infra`/`frontend`/`update-eval-history` all passed, the
`deploy` job correctly paused on the `production` environment, a human
approved it in the GitHub UI, and the job then failed immediately with
`Could not assume role with OIDC: Not authorized to perform
sts:AssumeRoleWithWebIdentity`. The trust condition's `sub` claim used
the ref-based shape (`repo:<org>/<repo>:ref:refs/heads/main`) -- the
form most OIDC-to-AWS guides show first -- but GitHub replaces that
shape entirely once a job references `environment:` (as `deploy` does,
for the very approval gate that had just correctly paused it), issuing
`repo:<org>/<repo>:environment:<name>` instead. Fixed in
`cicd_stack.py` to match the environment-based shape instead of the
ref-based one -- which, incidentally, ties the OIDC trust and the
human-approval gate together more tightly than originally intended:
the *only* way to reach this role is now a job running under the
`production` environment specifically, so an attacker who somehow got a
workflow onto `main` still couldn't assume it without also clearing
that same approval gate. Redeployed live; `aws iam get-role` confirms
the corrected `sub` condition against the real role.

**Second guess, same failure -- the real fix needed a decoded token,
not a better guess.** The environment-shaped claim (still using plain
`owner/repo` names) failed the exact same way on the very next real
approval. Guessing a third shape from documentation would have been
the same mistake twice over, so instead a temporary debug step was
added to `ci.yml`: fetch the real OIDC token via
`ACTIONS_ID_TOKEN_REQUEST_URL`/`ACTIONS_ID_TOKEN_REQUEST_TOKEN` (both
auto-populated by `permissions: id-token: write`, no extra config
needed) and decode its JWT payload directly. The real `sub` GitHub
issues for this exact job:

```
repo:tosspro23-cell@231253569/aws-healthcare-agent@1355988718:environment:production
```

-- each name carries its *immutable numeric ID* inline
(`owner@owner_id`, `repo@repo_id`), a shape not shown in the
ref-based-vs-environment-based examples this project had been going
by. Confirmed the two IDs independently against the live GitHub API
(`gh api repos/tosspro23-cell/aws-healthcare-agent --jq '.id,
.owner.id'`) rather than trusting a single decoded token alone.
`cicd_stack.py`'s constructor now takes explicit
`github_owner`/`github_owner_id`/`github_repo_name`/`github_repo_id`
parameters (defaulting to this repo's real values) and builds the sub
claim from all four -- the debug step removed from `ci.yml` once this
was confirmed, and `test_cicd_stack.py` gained a dedicated regression
test asserting the default parameters produce this *exact* string, not
just that some plausible-looking condition exists. Redeployed live a
second time; `aws iam get-role` now shows the condition matching the
decoded token byte-for-byte. The next push to `main` is the actual
end-to-end confirmation that a deploy can complete this way -- not yet
observed as of this entry, after two wrong guesses already corrected
here rather than left in as "should work."

**Third failure, one layer past the OIDC fix -- the trust condition
was right, the role just had no permission for a step that doesn't go
through `cdk deploy` at all.** The very next approval got past
`sts:AssumeRoleWithWebIdentity` cleanly (confirming the second fix
actually worked), then failed one step later: `ci.yml`'s own "write
frontend/.env.local" step calls `aws cloudformation describe-stacks`
*directly* with the OIDC-derived credentials, not through one of the
four bootstrap roles `cdk deploy` itself knows how to assume --
`AccessDenied ... is not authorized to perform
cloudformation:DescribeStacks`, confirmed via `gh run view --log`, not
assumed from reading the workflow alone. Fixed by granting
`GitHubActionsDeployRole` a direct, read-only
`cloudformation:DescribeStacks` grant scoped to exactly the two stacks
that step reads (`CareAgentAuthStack`, `CareAgentApiStack`), not a
broader `cloudformation:*` action or an account-wide resource pattern
-- the only concession this stack makes to "almost no direct
permission," and only because a plain describe call has no
bootstrap-role equivalent the way the actual deploy does. cdk-nag
flagged the resulting ARNs' trailing `/*` as `AwsSolutions-IAM5`;
suppressed with a `RegexAppliesTo` matched to exactly those two ARN
shapes, on the same reasoning already applied to the evidence bucket's
object-key wildcard elsewhere in this project: there is no way to name
a CloudFormation stack's resource without a trailing wildcard for its
stack-id suffix, which changes on every replacement. `test_cicd_stack.py`
gained a matching regression test asserting exactly one
`DescribeStacks` statement with exactly two resources, one per stack
name. Redeployed live; `aws iam get-role-policy` confirms the new
statement present and scoped to exactly those two ARNs.

## 2026-09-06 — Automated docs/EVAL_HISTORY.md updates on every push to main

**Context**: The capability eval (previous entry) shipped with
`scripts/update_eval_history.py` as a manually-run regenerator, the same
pattern `scripts/run_examples.py` already used -- deliberately, at the
time, to match existing convention. In practice that means the pass-rate
history it exists to build only grows when someone remembers to run it,
which defeats the actual point (a real trend over time, not a single
snapshot from whenever the harness was built).

**Decision**: a new `update-eval-history` CI job, gated to `push` events
on `main` specifically (not PRs -- nothing should auto-commit to a
branch nobody asked for) and `needs: smoke` (so it only runs once the
capability eval has actually passed). Runs the same regenerator script
and, if `docs/EVAL_HISTORY.md` changed, commits and pushes it back as
`github-actions[bot]`, with a GitHub Actions skip-ci marker in the
commit message so that push doesn't re-trigger this same workflow --
without it, the bot's own commit (a `push` to `main` too) would start
another run of this exact job, looping forever. Scoped with its own
`permissions: contents: write` at the job level rather than widening the
whole workflow's default permissions, the same least-privilege instinct
this project already applies to IAM.

**A live mistake, not a hypothetical, caught immediately**: this entry's
own first commit description spelled the skip marker out literally in
its body text, to explain the mechanism -- GitHub Actions' detection is
a plain substring match against the whole commit message, with no
awareness of whether the text was meant as an instruction or as prose
describing one. That commit's own CI run was silently skipped entirely
(confirmed via `gh run list` / the GitHub API showing zero check runs
against it), including the very job this entry is about. Rewritten here
to describe the marker instead of spelling it out.

**Verification**: `.github/workflows/ci.yml` YAML validated with
`python -c "import yaml; yaml.safe_load(...)"`. Live behavior (does the
job actually commit, and does its own commit correctly avoid
re-triggering CI) confirmed on this commit's own push -- the first one
whose CI run wasn't accidentally skipped by the mistake above.

---

## 2026-09-06 — Capability-based regression eval (`care_agent.eval`), and a real bug it found on its first run

**Context**: With the post-review punch list closed (backlog items,
frontend tests, cost protection), the next-highest-leverage gap wasn't
another independent review pass -- it was that `safety.py` has real
guardrails, but nothing verified the agent's *quality*/*capability*
claims kept holding as the kernel changed. `data/sample_questions.json`
has carried an `expected_capabilities` field since Phase 0 (e.g.
"uses_bloodwork", "does_not_diagnose") -- pure documentation, never
programmatically checked by anything. Distinct from `safety.py` (checks
one answer's own text) and from `tests/` (pins down narrow, specific
behaviors): this runs the *real* end-to-end agent against a curated
question set and checks whether each question actually demonstrated the
capabilities it exists to test.

**Decision**: a new `care_agent.eval` module, a registry mapping each
capability label to a check function that inspects the real
`AgentResponse`/`AgentTrace` (never the question's wording, never a
hardcoded expected-answer string, so it works unchanged against either
narrator backend). Capability labels that are genuinely context-specific
rather than a narrator-agnostic property of the response (e.g. "uses the
previous panel *if one is available* for this specific user's data") are
listed in `NOT_AUTOMATICALLY_CHECKABLE` and explicitly skipped, not
faked with an always-passing check -- the same "honest about limits"
principle `safety.py`'s own docstring already uses. `data/sample_questions.json`
expanded from 3 to 8 questions for full intent coverage (all five:
`priority_focus`, `trend_check` -- both with and without prior data,
`supplement_safety`, `general_bloodwork_question`, `red_flag_emergency`)
plus two deliberate pressure-tests: a direct "do I have diabetes?" and a
direct "how many mg of X should I take?", aimed squarely at the two
hardest safety checks to satisfy under direct pressure rather than by
accident.

**Wired in three places, not just one**: `tests/test_eval.py::test_all_sample_questions_pass_their_expected_capabilities`
runs the full suite as a real `pytest` assertion (so a regression fails
the existing test suite immediately, on every push, with no extra CI
step needed); a new `python -m care_agent eval-capabilities` CLI command
(mirroring `eval-samples`'s existing shape) for humans and for running
against `CARE_AGENT_NARRATOR_BACKEND=bedrock` by hand -- an LLM eval run
costs real tokens, so it's deliberately not CI-gated the way the free
mock-narrator run is; and `scripts/update_eval_history.py`, which
regenerates a new dated entry at the top of `docs/EVAL_HISTORY.md` (the
same "regenerate a checked-in doc from the live agent" pattern
`scripts/run_examples.py` already established, applied to eval results
instead of raw example output) -- a running history of pass rate over
time, not just a pass/fail snapshot of the current commit.

**A real regression this caught on its very first run, not a
hypothetical**: the new `q_trend_available` question ("Is my LDL getting
worse compared to my last panel?") failed `numeric_grounding` against
the deterministic mock narrator's own output --
`"Your LDL-C was 162 mg/dL on 2026-05-06, higher than the 148 mg/dL
result from 2025-12-08."` -- because yesterday's cross-marker-binding fix
(see the 2026-09-05 entry above) required the marker's name within a
fixed 40-characters-before/20-after window of each value, and "LDL-C" is
51 characters before the second value in this exact, entirely legitimate
sentence. Fixed by scoping the proximity window to the *current sentence
or line* (bounded by `.`, `!`, `?`, or a newline, with a 200-character
backstop for text with no punctuation at all) instead of a fixed
character count: a comma-heavy sentence naming its marker once still
covers every value in it, while the priority-focus narrator's actual
one-marker-per-line bulleted format keeps each line's value from seeing
a *different* marker's name on the adjacent line -- which a wider fixed
window would have let bleed through, undoing yesterday's fix for the
exact cross-marker case it exists to catch. `verify_numeric_grounding`'s
existing cross-marker tests (`test_numeric_grounding_closes_the_cross_marker_binding_gap`
and its companion) were re-run to confirm the new sentence-scoped window
doesn't reopen that gap -- both still pass.

**Verification**: full kernel suite (168 tests, up from 157; coverage
90.22%, gate 85%) passes, including `test_eval.py`'s eleven tests (unit
tests for individual capability checks, a drift guard confirming every
capability label used in the data file is either checked or explicitly
skipped, and the full end-to-end regression assertion). `eval-capabilities`
CLI run manually: 21/21 checks passed, 3 skipped, against the current
commit. `docs/EVAL_HISTORY.md` created with its first entry. Not yet run
against a live Bedrock narrator as of this entry -- that's a deliberate
follow-up, not free to run automatically.

## 2026-09-05 — Bedrock cost protection: API-wide throttle + an opt-in monthly Budget alert

**Context**: The last item in the post-review cleanup punch list. This
app has no per-user rate limiting of its own, and every route is either
free/cheap (API Gateway, Lambda, DynamoDB) or billed per-token via
Bedrock (`/ask`, `/runs`, `/jobs`) -- a leaked credential or a client bug
stuck in a retry loop had no in-app ceiling on how fast, or how much,
Bedrock spend it could run up. Two independent, complementary layers:
one caps the *rate*, the other catches the *trend*.

**Decision, API throttle**: `ApiStack`'s `HttpApi` uses API Gateway v2
(`HttpApi`), not the v1 REST API's Usage Plans + API Keys -- the v2
equivalent is a stage-level `DefaultRouteSettings` throttle
(`ThrottlingRateLimit`/`ThrottlingBurstLimit`), 5 req/s sustained, burst
10, applied uniformly across every route. Getting there took a live
mistake, not just a design choice: `HttpApiProps` has no way to pass
`throttle` through to the default stage it auto-creates, so the first
attempt set `create_default_stage=False` and created a new, explicit
`HttpStage` construct with `stage_name="$default"` -- which CloudFormation
rejected outright once deployed (`Resource of type
'AWS::ApiGatewayV2::Stage' ... already exists`), since the new construct's
logical ID differs from the one the already-deployed auto-created stage
uses, and CloudFormation won't let two different logical resources both
claim the physical stage name `$default`. Confirmed by inspecting the
live stack's actual `LogicalResourceId` before retrying. Fixed with an L1
property override (`add_property_override("DefaultRouteSettings...", ...)`)
on `http_api.default_stage`'s underlying `CfnStage` instead -- same
logical ID as what's already deployed, so CloudFormation applies it as an
in-place property update, not a replacement.

**Decision, monthly Budget alert**: an `AWS::Budgets::Budget` ($10/month,
COST type) with two notifications -- `ACTUAL` at 80% (something already
happened) and `FORECASTED` at 100% (the current trend would exceed the
budget by month's end, an earlier warning than waiting for actual spend
to cross the line). **Deliberately opt-in, not always deployed**: a new
`CARE_AGENT_BUDGET_EMAIL` env var (unset by default) gates whether
`BudgetStack` gets built into the app at all -- an email address is
committed nowhere in source, since this repository is public and an
email is more personal than the account IDs this project already avoids
hardcoding (see this file's earlier ADR on `auth_stack.py`'s domain
prefix). A budget with no meaningful subscriber isn't worth deploying at
all, so the stack is simply absent rather than half-configured when the
env var isn't set -- confirmed both ways via `cdk synth --all`
(`CareAgentBudgetStack` appears in the stack list only when the env var
is set).

**Verification**: New tests for both -- `test_default_stage_has_a_throttle_configured`
(the throttle exists; the exact numbers are a judgment call, not a
security invariant, so not pinned to specific values) and four
`test_budget_stack.py` tests (a positive monthly limit, the configured
email -- not a placeholder -- on every notification, both notification
types present, exactly one budget resource). Full infra suite (152
tests, up from 148) and `cdk synth --all` (both with and without the env
var) pass. Deployed live: `aws apigatewayv2 get-stage` confirms
`ThrottlingRateLimit: 5.0` / `ThrottlingBurstLimit: 10` on the real
`$default` stage; a CORS preflight against the CloudFront origin still
succeeds (unaffected by the throttle fix, re-confirmed given the CORS
regression earlier this same day). `aws budgets describe-budgets` /
`describe-notifications-for-budget` / `describe-subscribers-for-notification`
confirm the live budget, both notification thresholds, and the correct
subscriber email -- AWS Budgets' email notifications need no separate
opt-in confirmation click, unlike an SNS topic subscription.

## 2026-09-05 — First frontend tests: Vitest for auth.ts's token expiry and AskForm.tsx's polling supersession

**Context**: The kernel package has run at 85%+ coverage since Phase 0;
the frontend has had zero automated tests since it was first built in
Phase 6. That asymmetry showed: findings 3, 4, and 8 of round 3's
independent review were all real bugs in `auth.ts`/`AskForm.tsx` found
only by manual live-browser verification, and each fix was likewise only
confirmed the same manual way. Picked as the third item in the post-
review cleanup punch list, after the two backlog closures above,
specifically because this logic (token expiry, polling generation
counting) has already been the source of real bugs and will be touched
again.

**Decision**: `vitest` + `jsdom` + `@testing-library/react` +
`@testing-library/jest-dom`, configured via `vite.config.ts`'s own
`test` block (no separate config file) since this project already uses
Vite -- no new build tool. Test files live next to their source
(`auth.test.ts`, `AskForm.test.tsx`), covered by the existing
`tsconfig.json`'s `"include": ["src"]` without change. `config.ts`'s
`requireEnv` throws immediately at module-evaluation time if a `VITE_*`
env var is missing -- true during `vitest`'s real Node/jsdom test
execution (unlike `vite build`, which never actually runs this
module-level code, only bundles it) -- so a new `.env.test` with dummy,
non-sensitive values is committed (unlike `.env.local`, which holds real
deployed identifiers and stays gitignored).

**Scope, deliberately narrow**: two files, not a push for blanket
coverage. `auth.test.ts` covers `getAccessToken`'s self-expiry check,
`handleSessionExpired`, and `signOut` -- pure logic plus `sessionStorage`/
`localStorage`, no rendering needed. `AskForm.test.tsx` covers exactly
one scenario: a poll for one run still has a request in flight when the
user selects a *different* run from history, and the stale response must
not overwrite the newer run's state once it finally resolves -- the
precise race finding #3 fixed. Getting there took two false starts,
kept as the comments in the test file explain: the obvious way to
reproduce "start a new run while the old one is pending" is resubmitting
the form, but the submit button is correctly disabled while a run is
pending, so that path is unreachable; the actual trigger is
`handleSelectHistoryEntry`, which isn't gated by `pending` at all.
Getting the deferred-promise sequencing right also required noticing
that `handleSelectHistoryEntry` calls `getRun` directly (blocking,
`asyncResult` stays `null` until it resolves) before ever scheduling a
poll tick -- unlike `handleSubmit`'s async paths, which show an
optimistic state immediately. **Verified the test actually catches the
regression it claims to**, not just that it passes: temporarily removed
the in-flight generation check the test targets, confirmed the test
failed (the stale response visibly clobbered the display), then restored
it and confirmed green again -- the same discipline as this project's
kernel-side regression tests, just newly possible on the frontend.

**CI**: a new `frontend` job in `.github/workflows/ci.yml` (lint,
`vitest run`, `tsc -b && vite build`) -- these tests would otherwise rot
silently, checked by no CI, the same gap that let the frontend go this
long with zero coverage in the first place.

**Verification**: `npm test` (7 tests, 2 files) passes; `tsc -b`,
`npm run build`, and `npm run lint` all clean; the production bundle's
content hash is unchanged from before this change (test files and
config are never part of the app's own import graph). Not yet pushed
through CI as of this entry.

## 2026-09-05 — Closed the two deliberately-open backlog items: cross-marker value/unit binding, SQS processing lease + DLQ reconciliation

**Context**: Round 2's independent review found both gaps but explicitly
left them open as backlog items needing "a real design change, not a
quick patch" (see `docs/INDEPENDENT_REVIEW_FINDINGS.md`, findings #6 and
#9), and every later review round was told not to re-flag them unless
risk looked underestimated. With round 3's frontend/hosting findings all
closed, these were the highest-value remaining known gaps -- picked up
directly rather than running a fourth blind review pass.

**Decision, cross-marker value/unit binding (finding #6)**: value+unit
grounding (`safety.verify_numeric_grounding`) checked only that *some*
`GroundedFact` carries the exact (value, unit) pair in the text, not that
the text's claimed marker is the one that actually has it -- several
markers share a unit (LDL-C/HDL-C/triglycerides/fasting glucose are all
`mg/dL`), so "Your LDL-C is 188 mg/dL" passed even when 188 was only ever
grounded as Triglycerides. Closed without the full structured-claim
rewrite the review suggested (bind concept + value + unit + date
together and validate *that*, not free text): `GroundedFact` gained an
optional `display_name` field, populated at every real construction site
in `agent.py` (biomarker focus items, mentioned markers, and -- via
`brief.mentioned_markers`, already resolved earlier in the same method --
both trend-fact variants) directly from the source `Biomarker.display_name`,
never re-derived from free text. When a value+unit match's candidate
fact(s) carry a `display_name`, that exact name must now appear within a
40-characters-before/20-after window of the match (generous enough for
every phrasing this project's own narrators produce, including
Bedrock's slightly longer prose, without being so wide it'd accept a name
mentioned in an unrelated sentence) -- if it's a fact with no
`display_name` (a panel-age fact, a questionnaire claim -- neither has a
single "marker name" to check against), the check stays exactly as
permissive as before, so this only tightens markers the pipeline already
knows how to name and introduces no new class of false positive.

**A second gap in the same finding, fixed in the same pass**: the
value+unit regex had no way to capture a leading minus sign, so
`"-162 mg/dL"` was silently reinterpreted as the unsigned `162` and
matched a real, positively-grounded value. Both `_VALUE_UNIT_RE` and
`_NUMBER_RE` now optionally capture a leading `-`; the existing lookbehind
on `_NUMBER_RE` still correctly declines to treat the hyphen in a
hyphenated identifier (`"test-162"`) as a sign, since that position is
still excluded the same way it always was.

**An existing doc overclaim caught while touching this code**: the
module docstring already asserted check 4 "cannot introduce a new
number, or reattach a real number to the wrong marker" -- true only after
this fix; before it, that was exactly finding #6's gap. Corrected to
state the real, narrower scope (closes the wrong-marker bypass for any
fact carrying a `display_name`; a fact without one still only gets the
weaker value-only check).

**Decision, SQS processing lease + DLQ reconciliation (finding #9)**:
two problems, both in the queue-buffered async path. First,
`process_job.py`'s conditional RUNNING-write allowed `RUNNING -> RUNNING`
(meant to tolerate a legitimate redelivery of a message whose prior
attempt already finished or crashed), but that same allowance can't
distinguish that from a second, *genuinely concurrent* delivery for the
same run_id (a client's own retry against `enqueue_job.py` creating a
second, independent message) -- both would call the agent, doubling
Bedrock cost and racing on the terminal write. Fixed with a
`processing_lease_expires_at` field: a RUNNING record can only be
re-claimed once its lease has actually expired. The lease duration (35s)
is set a few seconds past the handler's own Lambda timeout (30s) --
deliberately *not* tied to the queue's 90s visibility timeout, a
different, unrelated margin (see `queue_stack.py`'s own comment) --
short enough that a genuinely crashed invocation is reclaimable soon
after, long enough that a still-running invocation's lease can never
expire out from under it (Lambda hard-kills at 30s, so no invocation can
physically outlive a 35s lease). DynamoDB's atomic compare-and-swap on
the claim's `ConditionExpression` guarantees exactly one concurrent
caller ever wins, regardless of timing -- no fencing token needed.

Second, a message that exceeded `_MAX_RECEIVE_COUNT` and moved to the
dead-letter queue left its run_id with nothing in the system that would
ever write it a terminal status -- the record just stays wherever
`process_job.py`'s last attempt left it, and a caller polling
`GET /runs/{run_id}` waits forever. A new `reconcile_dlq.py`, triggered
by the DLQ itself (`ReconcileDlqHandler` in `queue_stack.py`), marks the
record `FAILED`, conditioned on it still being non-terminal (`QUEUED` or
`RUNNING`) so a legitimate outcome that happened to land right as the
redrive fires is never clobbered.

**A small refactor along the way**: both `process_job.py` and
`reconcile_dlq.py` need the identical reserved-keyword-safe conditional
UpdateExpression shape, so it's factored into a new shared
`run_writes.py` rather than duplicated -- the first time this project has
shared Lambda-handler code across files rather than each handler writing
its own bespoke version (the Step Functions path's handlers still do,
since each has only a single fixed condition, not a variable
`if_status_in` set). Both `process_job.py` and `run_writes.py`
consistently call through `run_writes._dynamodb()` (never re-imported
into `process_job`'s own namespace via `from ... import`) specifically so
a single `patch("run_writes._dynamodb")` in tests intercepts every write
path uniformly, regardless of which file's code triggers it -- the
straightforward `from run_writes import _dynamodb` alternative would
have created two independently-patchable name bindings and silently
broken the existing `test_process_job_marks_running_before_finishing`
spy test.

**Verification**: New tests for both fixes -- cross-marker: the exact
swapped-marker scenario now correctly fails, a companion test confirms
the correctly-attributed case still passes, a fact without
`display_name` keeps the old permissive behavior, and the negative-sign
case is rejected. Queue: a concurrent delivery arriving while the lease
is valid is rejected without ever calling the agent; a record whose
lease has since expired is successfully reclaimed and completed; all
three DLQ-reconciliation outcomes (stuck `QUEUED`, stuck `RUNNING`, and
an already-`SUCCEEDED` run that must not be clobbered) pass. Full kernel
suite (157 tests, up from 154) and full infra suite (147 tests, up from
140) pass; `ruff`/`mypy` clean on both; `cdk synth --all` (6 stacks)
clean.

Deployed live (`cdk deploy --all`, with `CARE_AGENT_WORKBENCH_URL` set
this time to avoid repeating the earlier CORS regression -- confirmed
via a live preflight that the CloudFront origin is still allowed).
**Live-verified against the real deployed account, both fixes at once**:
seeded a `QUEUED` record directly in DynamoDB and sent a matching message
straight to the real SQS queue (bypassing the frontend, exercising
exactly `process_job.py`'s production code path) -- the record reached
`SUCCEEDED`, `safe: true`, `narrator_backend: "bedrock"`, with a real
Bedrock answer ("LDL-C 162 mg/dL... HbA1c 6.1%... Fasting glucose 108
mg/dL") that passed the new marker-name-proximity check against actual
LLM prose, not just the deterministic template's fixed phrasing. Then
sent *two* messages for the same run_id within the same second: the
record still reached a single consistent `SUCCEEDED`, and CloudWatch's
own `REPORT` lines for `ProcessJobHandler` in that window showed three
invocations -- two ~4-4.7s (real Bedrock calls, one from each seeded
run) and one at 212ms, far too short to have called Bedrock, confirming
the lease correctly rejected the second concurrent delivery for the
race-tested run_id before ever reaching `_agent.ask()`. Both throwaway
test records were deleted afterward. DLQ reconciliation itself was not
separately forced live (deliberately: manufacturing three real failed
deliveries against production Bedrock/IAM risks disrupting the account
for a scenario moto's tests already cover exactly) -- code- and
test-verified only for that specific path.

---

## 2026-09-05 — Third independent review: a reopened safety bypass, a within-topic overclaim, and four frontend fixes

**Context**: Requested specifically to cover what round 1 and round 2
never touched -- the entire frontend and the newly-public S3+CloudFront
hosting from the same day's earlier Phase 6 work -- plus verification of
several hotfixes made live during that work. All 8 findings were
independently reproduced against this repo's own code before being fixed
here, the same standard rounds 1 and 2 used.

**Finding 1 (High, safety bypass reopened)**: A live hotfix made earlier
the same day added a `question_text` exemption to
`verify_numeric_grounding` -- a bare number was accepted as grounded if
the caller's own question already used it, meant to stop a real false
positive where the model declined while citing the question's own
number. The review reproduced two ways this reopened a genuine
fabrication bypass: `"Is my cardiovascular risk score 999?"` ->
`"Your... risk score is 999."` now passed even though 999 is never a
real grounded value (the exemption can't distinguish declining-while-
citing from affirming-while-fabricating); and irregular spacing
(`"500  mg/dL"`, two spaces) or Markdown emphasis (`"**500** mg/dL"`)
made the *strict* value+unit regex fail to match, silently falling
through to the now-exempted weak path instead of being checked against
real grounded values at all. **Decision: reverted the exemption
entirely** rather than patching the regex further -- reliably telling
"declining while citing a number" from "asserting that number as fact"
isn't solvable with a regex, and the asymmetry matters: a false positive
here just means a safe answer gets replaced by the deterministic
template, while a false negative means a fabricated clinical number
reaches the user. Confirmed via direct reproduction in Python before and
after the revert. `run_safety_checks`'s `question_text` parameter was
removed along with it (both `agent.py` call sites updated).

**Finding 7 (Medium, within-topic overclaim)**: `mock_narrator.py`'s
closing personalization summary hardcoded `"nutrition" in topics or
"exercise_volume" in topics` into one combined phrase ("leans on your
stated food and activity preferences") regardless of which of the two
actually fired -- the same "policy written for one case, applied to a
different case" failure mode round 2 already fixed for other topics, not
extended to this pair. **Decision**: split into two independent
branches, each using the specific detail already present in that
modifier's own grounded-fact claim (a new `_claim_detail()` helper) so
the visible sentence names the actual reported signal instead of an
assumed one. **A real regression introduced while fixing this, caught by
the full suite before considering the fix done**: the `exercise_volume`
modifier's claim text ("...less than 60 minutes...") embeds a literal
number never registered as grounded, so reusing that text in the closing
summary broke `numeric_grounding` for 10 previously-passing tests
(`ungrounded numbers: ['60']`). Fixed in `reasoning.py` by adding
`numeric_values=_numbers_in(claim)` to that fact's construction, the
same pattern finding #8 of round 2 already established for exactly this
situation.

**Findings 2-5, 8 (frontend, never previously reviewed)**: (2) Run
history (`localStorage`, full question text) persisted across accounts
in a shared browser -- `signOut()` now also calls a new
`clearHistory()`. (3) `AskForm`'s polling used `setInterval(async () =>
...)`, which doesn't wait for the previous tick's request to resolve --
a slower, earlier response could resolve after a faster, later one and
overwrite already-terminal state with stale data. Rewritten as a
self-scheduling `setTimeout` (next tick only scheduled after the current
one resolves) plus a monotonic generation counter checked before and
after each async call, so a superseded poll loop's in-flight request can
never write state again. (4) A polling error that wasn't tolerated (not
the expected transient 404 right after a Step-Functions start) set
`error` but never reset the derived `pending` flag, permanently
disabling submit and mode-switching with nothing left running; a new
`pollingStalled` state overrides `pending` in that case. `handleCancel`
also now applies the real post-cancel state immediately (refetching via
`getRun`) instead of waiting for a poll tick that might never come if
polling had already stopped or the run raced to a terminal state.
(5) `react-markdown` renders `![alt](url)` as a real `<img>` the browser
eagerly fetches with no user interaction -- a data-exfiltration vector
for any app whose text ultimately originates from an LLM completion.
Given a `components` override for `img` (same pattern already used for
`a`), rendering a plain `[image: alt -- not rendered]` badge instead.
(8) A stored access token was treated as valid regardless of age; an
expired token just failed every API call with a 401 the app never
noticed, leaving the signed-in Workbench displayed indefinitely.
`completeSignIn` now records `Date.now() + expires_in * 1000` alongside
the token; `getAccessToken()` self-clears an expired one, and
`authedFetch` forces a hard redirect to `/` on any 401 (a revocation
server-side can't be caught by the client's own expiry bookkeeping
alone).

**Finding 6 (get_run.py, deployed before the rest of this round)**: the
`AccessDenied`-for-missing-trace fix from the same day's earlier work
only wrapped the `get_object()` call itself in its `try` block --
reading the response body (`.read()`, which can raise `ReadTimeoutError`,
a `BotoCoreError` subclass with no `.response` attribute, distinct from
`ClientError`) and `json.loads()`-ing it happened *after* the `except`,
unprotected. A transport-level failure while streaming an otherwise-
successful `get_object()` response still 500'd the whole `GET /runs/
{run_id}` endpoint. Fixed by moving both calls inside the `try` and
catching `BotoCoreError`/`ValueError` alongside `ClientError`.

**A self-inflicted deploy bug, caught during this round's own live
verification, not left in**: redeploying all six stacks with `cdk
deploy --all` and no `CARE_AGENT_WORKBENCH_URL` set silently reset
`ApiStack`'s CORS `allow_origins` back to its bare default
(`["http://localhost:8765"]`, see `app.py`'s two-pass deployment
pattern), dropping the live CloudFront origin from the allowlist and
breaking every API call from the public URL with a browser-level
`TypeError: Failed to fetch` (a preflight `OPTIONS` response with zero
CORS headers). Caught immediately by testing the redeployed public URL
in a real browser rather than only the local dev server. Fixed by
redeploying `CareAgentAuthStack` and `CareAgentApiStack` a second time
with `CARE_AGENT_WORKBENCH_URL` set to the live `WorkbenchUrl` output --
confirmed via a live CORS preflight (`curl -X OPTIONS`) that
`Access-Control-Allow-Origin` for the CloudFront origin is present
again. This is a deployment-process gap worth remembering, not a code
bug: `app.py`'s docstring already documented the two-pass requirement,
but nothing enforces it at deploy time -- a future `cdk deploy --all`
run the same way will reproduce this exact regression.

**Verification**: Full kernel suite (154 tests, up from 152 before
finding 7's fix; coverage 90.67%, gate 85%) and full infra suite (140
tests, up from 139) pass; `ruff`/`mypy` clean on both; frontend
`npm run build` and `cdk synth --all` (6 stacks) both clean. All 6
stacks redeployed live. Live-verified in a real browser against both
`localhost:8765` and the public CloudFront URL: history is empty
(`localStorage.getItem(...)` returns `null`) immediately after sign-out
(finding 2); a real Step-Functions run's `Cancel this run` raced against
natural completion, returned `409: Run was already finalized`, and the
UI correctly displayed the real `SUCCEEDED`/`SAFE` terminal state instead
of getting stuck (finding 4, and indirectly finding 3 -- the same run
polled cleanly through `RUNNING` to a terminal state with no stale
overwrite); an explicitly-expired token blocked a submit with a clean
"Not signed in." before ever calling `fetch` (finding 8, forced); and,
unforced, a real token that had aged out over the course of this
session's own work hit the live redeployed API, got a real 401, and
`handleSessionExpired()` correctly forced a clean return to the sign-in
screen rather than leaving the signed-in shell stuck -- the same fix
confirmed twice, once deliberately and once by accident. Finding 5 (the
image-suppression badge) is verified by code reading and the identical,
already-proven `a`-override pattern, not by a live malicious-image
probe. Finding 1's revert is verified via direct reproduction and the
kernel test suite (including a Bedrock-narrator-backed regression test),
not via a live Bedrock call deliberately trying to reproduce the exact
fabrication -- inherently non-deterministic to force on demand, and no
previous round's safety-check verification relied on that either.

---

## 2026-09-05 — Phase 6 finished out: async trace persistence, markdown rendering, public hosting

**Context**: Requested as the last increment on the Workbench: (1) give
the async paths the same full grounding trace the sync path already has,
(2) render Bedrock's markdown prose instead of showing literal syntax,
(3) host the Workbench somewhere real instead of only `npm run dev`,
explicitly as "a step toward a real user-facing product" -- with two
requirements attached: self-sign-up must be off first (a public link
shouldn't let strangers create accounts, even against synthetic data),
and the layout needed to actually work on a phone, not just a desktop
dev browser.

**Decision, trace persistence**: `agent_task.py` and `process_job.py`
now write their full trace to the same `{run_id}.json` S3 key
`adapter.py`'s synchronous path already used, with the same
precisely-scoped `s3:PutObject` grant pattern this project has used
throughout. `get_run.py` opportunistically merges that trace in under a
`trace` key.

**A second real bug found live, not a hypothetical**: `get_run.py` is
deliberately granted only `s3:GetObject` (not `s3:ListBucket`, which
would let it enumerate every run_id's evidence in the bucket -- a much
bigger permission than "read one object I already know the key for").
Without `ListBucket`, S3 can't tell a caller whether a missing key
doesn't exist or is merely inaccessible, so it returns `AccessDenied`
instead of `NoSuchKey`/404 for a run whose evidence hasn't been written
yet. This propagated uncaught and 500'd the *entire* `GET /runs/{run_id}`
response -- including the DynamoDB status/answer, which were perfectly
fine and had nothing to do with the missing trace. Reproduced against the
real deployed account (moto doesn't enforce IAM, so this couldn't have
been caught there) via the Workbench's own polling hitting it mid-run.
Fixed by treating *any* S3 read failure for this specific, best-effort
enrichment as "no trace yet" -- it sits on top of DynamoDB's record,
never replaces it as the source of truth, so a failure here should never
be allowed to take down the whole response.

**Decision, markdown rendering**: `react-markdown` (never executes raw
HTML -- parses to React elements, appropriate since this text ultimately
originates from an LLM completion, not fully trusted content even though
the safety pipeline already constrains its factual claims).

**Decision, public hosting**: `FrontendStack` (S3 + CloudFront, Origin
Access Control, no public bucket policy, no website-hosting endpoint) --
built via a `build_frontend_asset.py` mirroring `build_lambda_asset.py`'s
existing pattern. Self-sign-up disabled on the User Pool
(`self_sign_up_enabled=False`) before the URL went live -- confirmed
visually (the Hosted UI's "Sign up" link is gone) and via
`AllowAdminCreateUserOnly: true` on the real user pool; no test or flow
in this project ever depended on self-sign-up staying on. A mobile pass
(flex-wrapping mode tabs, 16px form inputs to avoid iOS Safari's
zoom-on-focus, 44px touch targets, a narrow-viewport media query) --
verified on an emulated 375px viewport against the real hosted URL.

**The two-pass deployment problem**: `FrontendStack`'s CloudFront domain
isn't known until after its first deploy, but `AuthStack`'s Cognito App
Client and `ApiStack`'s CORS both need that exact domain registered
before a browser served from it can complete a real login or call the
API. Solved with an optional `CARE_AGENT_WORKBENCH_URL` env var
`app.py` threads into both stacks -- unset for the first deploy, set to
the printed `WorkbenchUrl` output for the second. The frontend's own
redirect/logout URI is derived from `window.location.origin` rather than
hardcoded, so the exact same build works unmodified on `localhost:8765`
and the hosted URL -- no separate "production build" was needed.

**Verification**: Full kernel/infra test suites pass (infra: 139 tests,
up from 130, including new `test_frontend_stack.py` and regression tests
for both the trace-merging behavior and the `AccessDenied` fix). `cdk
synth --all` succeeds (6 stacks now). Deployed live in two passes;
confirmed via `describe-user-pool-client` that both `localhost:8765` and
the CloudFront URL are registered as callback/logout URLs, via
`describe-user-pool` that `AllowAdminCreateUserOnly` is `true`, and via a
live CORS preflight that the CloudFront origin is allowed. End-to-end in
a real browser against the live public URL: sign-in through the actual
Cognito Hosted UI (a human completed the credential entry, per this
project's standing constraint), a real `/ask` call with markdown
rendering, a Step-Functions run showing its full trace after the
`get_run.py` fix (reproduced the 500 live first, then confirmed the fix
against the same run_id), and the whole page checked on an emulated
375px mobile viewport.

---

## 2026-09-05 — `cancel_run.py`'s two conflict responses used "message" instead of "error"

**Context**: User-reported from the Workbench: a `Cancel this run` click
that lost the race (the run had already finished, or was a synchronous
run that can't be cancelled at all -- both legitimate, correctly-detected
409 outcomes) showed a bare "409: Request failed with status 409" with no
explanation. Every other error response in this API -- `start_run.py`,
`enqueue_job.py`, `adapter.py`, `get_run.py`, and even `cancel_run.py`'s
own 404 -- puts the human-readable reason under an `"error"` key; only
these two specific 409 responses used `"message"` instead. The frontend
(correctly, matching the API-wide convention) reads `body.error`, so it
silently got nothing for exactly these two cases.

**Decision**: Renamed both fields from `"message"` to `"error"`, matching
every other response in the API. No client depended on the old key name
(checked: no test asserted on it).

**Verification**: New assertions in `tests/test_orchestration_lambdas.py`
(`test_cancel_run_refuses_to_cancel_a_synchronous_ask_run` and
`test_cancel_run_loses_race_when_already_finalized`) that `"error"` is
present in the response body. Full infra suite (130 tests) passes.
Redeployed `CareAgentOrchestrationStack` and live-verified directly
against the deployed `CancelRunHandler`: cancelling a
directly-seeded already-`SUCCEEDED` run now returns `{"error": "Run was
already finalized; nothing to cancel.", ...}`, not a bare status code.

---

## 2026-09-05 — Workbench: wired up the async paths (Step Functions + Queue), client-side run history

**Context**: The Workbench's first version covered only the synchronous
`/ask` path. Requested next: bring the async Step Functions and SQS
paths, cancellation, and persistence into the UI too, so the Workbench
covers what the backend phases actually built rather than just the
simplest path.

**Decision**: One form, a mode switcher (`Ask` / `Start run (Step
Functions)` / `Enqueue job (Queue)`) instead of three separate pages --
all three share the same user_id/question inputs and differ only in
which endpoint starts the run and whether polling is needed. The two
async modes poll `GET /runs/{run_id}` every second until a terminal
status, with a `Cancel this run` button visible while pending. Run
history is client-side only (`frontend/src/history.ts`, localStorage):
a `run_id` list per browser lets a past run be revisited via `GET /runs/
{run_id}` after a reload, which demonstrates real DynamoDB persistence
without building a new backend "list my runs" endpoint -- the runs
table's only key is `run_id`, so that would need a new GSI + Lambda +
route, a meaningfully larger piece of infra work than wiring up what
already exists. Left for later if it's ever worth doing.

**A real race condition found live-testing this**: `POST /runs` returns
as soon as Step Functions accepts `start_execution`, before its first
task (`mark_running.py`) has actually written the DynamoDB record --
polling immediately after start reliably produced a real `404`
(reproduced, not theoretical). Fixed by tolerating a bounded run of
404s (10 poll ticks) at the start of a poll loop rather than treating an
immediate fetch as authoritative, and by not doing a blocking `getRun`
call synchronously right after starting -- the UI shows an optimistic
pending state from the start call's own response instead. The SQS path
doesn't have this specific race (`enqueue_job.py`'s conditional create
happens synchronously before it returns 202), but the fix applies to
both paths uniformly rather than branching on `execution_type`, since
tolerating a transient 404 is harmless either way.

**Verification**: `tsc -b` and `eslint .` clean, production build
succeeds. Live end-to-end in a real browser (signed in through the
actual Cognito Hosted UI): both async modes correctly transitioned
`RUNNING`/`QUEUED` -> `SUCCEEDED` with the real Bedrock-backed answer;
clicking a past history entry after a full page reload correctly
re-fetched and displayed it, proving the data survives independent of
any client-side state. Cancellation was verified via the exact HTTP
calls the UI's `Cancel this run` button makes (a manual click reliably
lost the race against Bedrock's ~1-2 second response time -- a
UI-testing limitation, not a functional gap, and the underlying
conditional-write mechanism was already race-tested repeatedly earlier
in this project): started a run, cancelled it immediately, and confirmed
the record stayed `CANCELLED` -- not overwritten by the agent's own
completion -- when re-checked 3 seconds later, well past when it would
normally have finished.

---

## 2026-09-05 — Numeric grounding rejected a model correctly declining to fabricate a number

**Context**: Testing fallback behavior via the Workbench, asked Bedrock to
"calculate my 10-year cardiovascular risk score" -- something this
project's data and policies don't support computing. Bedrock did the
right thing: it declined, explicitly saying a real risk-score calculation
needs a validated clinical tool and more inputs than are available. This
safest-possible response still failed `numeric_grounding` and got
replaced by the (objectively worse in this instance) mock template --
because "10" (from the user's own "10-year" phrasing, referenced back
while explaining the refusal) matched no `GroundedFact`. The model hadn't
invented anything; it echoed a number the caller had already introduced.
User-reported directly from a live Workbench session, then independently
reproduced across several other questions (asking for reference ranges,
population comparisons) that provoke the same shape of false rejection.

**Decision**: `verify_numeric_grounding`/`run_safety_checks` now accept
the original `question_text` and add any bare (no-unit) number *the
caller already used* to the weak grounding set. Deliberately narrow:
this only touches the weaker, no-unit-attached check (already documented
as incomplete -- "unavoidable for numbers with no unit to bind against").
The strict value+unit path (e.g. "your LDL is 500 mg/dL") is completely
unaffected even if a question happens to mention that same number --
verified directly with a test that a false value+unit claim is still
rejected when the question also contains the number.

**Verification**: New tests in `tests/test_safety.py` (the exemption, and
that it doesn't weaken the value+unit path) and
`tests/test_bedrock_narrator.py` (full agent-level reproduction of the
exact reported scenario -- now `safe=True`, `narrator_backend="bedrock"`,
no fallback). Full kernel suite (152 tests, up from 149) and infra suite
(130 tests) both pass. Live-verified against the deployed `AskHandler`
after redeploying all 5 stacks: the exact reported question, run 3 times,
stayed on `bedrock` with no fallback every time. Also confirmed the
already-verified genuine fallback cases (asking for a reference range
with units, e.g. hs-CRP "1.0-3.0 mg/L") still correctly fall back --
the strict path is unaffected.

---

## 2026-09-05 — Fallback debug visibility, mechanical-sounding wording, and a red-flag gap for headaches

**Context**: Three separate pieces of feedback from testing the
Workbench directly: (1) when a fallback happened, there was no way to see
what the rejected draft actually said or precisely why -- the
`narrator_fallback` entry just said "failed a safety check," full stop;
(2) the composed answers still read as templated/mechanical in a couple
of specific spots; (3) "I'm having big head pain, what should I do?"
classified as `priority_focus`, not `red_flag_emergency` -- worth
checking whether that's a real gap.

**Decision, fallback visibility**: `AgentTrace` gained `rejected_draft:
str | None`, populated with the discarded narrator output whenever a
fallback happens; the `narrator_fallback` safety-check detail now names
which specific check(s) failed and why, not just "a safety check." Never
surfaced as the answer -- only as debug/trace information, consistent
with this project's existing "expose enough trace/debug information"
design goal.

**Decision, wording**: Two real issues found while investigating, not
just subjective polish: (a) `reasoning.py`'s exercise-limitation and
family-history modifiers (fixed earlier the same day to render the
caution's actual reported detail instead of a hardcoded specific claim)
embedded that detail text verbatim mid-sentence ("given the reported
exercise limitation: Reports knee pain..."), which reads grammatically
broken -- a `_naturalize_detail()` helper now strips the leading
"Reports "/trailing period and lowercases it for natural mid-sentence use.
(b) `mock_narrator.py`'s closing "Your questionnaire answers changed this
plan..." sentence was a single hardcoded string emitted whenever *any*
questionnaire modifier fired, unconditionally naming knee pain/sleep/
stress regardless of which modifiers actually applied -- only
coincidentally correct against the shipped sample data, where every
modifier always fires together. This is the same hardcoded-regardless-
of-trigger failure mode as several findings from the two independent
reviews, just not caught there since it lived in the narrator, not
`reasoning.py`. Rebuilt to name only the modifiers actually present,
joined with proper "A, B, and C" list grammar instead of a repeated
"; and" chain (which itself read mechanically once 3+ parts existed).

**Decision, red-flag headache gap**: `_RED_FLAG_PATTERNS` had no
headache-related coverage at all. Added three specific, medically-
established emergency-headache phrasings (`worst headache`, `sudden
severe headache`, `thunderclap headache`) -- deliberately not a bare
"headache"/"head pain" pattern, since an ordinary headache is common and
not itself an emergency; flagging every mention would make the system
wrongly tell people to go to the ER constantly, and breaks the existing
list's own scoping principle (specific established phrasings only, e.g.
"chest pain" is present but a generic "chest discomfort" is not). This
means a vague phrasing like "big head pain" deliberately still does not
trigger red_flag_emergency after this fix -- flagged to the user as an
explicit product-judgment boundary, not silently decided.

**Verification**: New tests in `tests/test_intent.py` (headache
red-flag, and the "big head pain" non-match documenting the boundary),
`tests/test_mock_narrator.py` (new file: personalization summary omitted
with no modifiers, and only naming modifiers that actually fired), and
an extended `tests/test_bedrock_narrator.py` fallback test asserting
`rejected_draft` and the enriched failure detail. Full kernel suite (152
tests) and infra suite (130 tests) pass. Live-verified against the
deployed `AskHandler`: "worst headache of my life" now returns
`red_flag_emergency` with an emergency-care answer; "big head pain"
still returns `priority_focus`, confirmed as the intended boundary, not
an oversight.

---

## 2026-09-05 — Found via the Workbench itself: "vitamin" hijacked trend questions into supplement_safety

**Context**: Testing the newly-built Workbench end to end, a real question
-- "Has my vitamin D changed since last time?" -- was classified as
`supplement_safety`, not `trend_check`. Cause: `intent.py`'s
`_SUPPLEMENT_PATTERNS` included a bare `\bvitamin\b` pattern, checked
before trend/priority patterns get a chance -- since "Vitamin D" is also
this project's biomarker name, *any* question naming that marker (trend,
priority, or otherwise) got force-classified as supplement_safety. Trend
computation (`compute_trend`) never ran, leaving `Brief.trend_result`
unset. The LLM narrator filled that gap with an unverified prose claim
("I don't have a previous vitamin D result to compare") -- which
happened to be factually correct this time (the earlier panel genuinely
has no Vitamin D reading, confirmed against `data/sample_bloodwork.json`
directly), but was never actually checked by anything in the pipeline.
This is a live instance of a more general, already-acknowledged gap: the
safety checks verify *numbers*, not arbitrary narrative/procedural claims
a narrator might add -- the same underlying limitation as the still-open
cross-marker value/unit binding gap. Not fixed further here; noted as the
same class of issue.

**Decision**: Split `_SUPPLEMENT_PATTERNS` into the genuinely strong
supplement/dosing signals (`supplement`, `dose`, `dosage`, `pill`,
`mg of`) and a separate, weaker `_MARKER_NAME_ONLY_PATTERNS` (`vitamin`
alone). The weak pattern only wins as `supplement_safety` when
trend/priority language isn't *also* present in the same question --
otherwise trend_check gets the chance it should have had. A bare
supplement question with no trend language ("what vitamin should I take
for my low levels?") is unaffected and still classifies as
`supplement_safety`, matching prior behavior.

**Verification**: New tests
`test_vitamin_d_trend_question_is_trend_check_not_supplement_safety` and
`test_vitamin_supplement_question_without_trend_language_is_still_supplement_safety`
(`tests/test_intent.py`) cover both directions. Full kernel suite (143
tests, up from 141) passes. Re-ran the exact original question locally
(`intent: trend_check`, and the answer's data-unavailability claim is now
sourced from `trend.reason_unavailable`, a real computed field, not an
LLM guess) and against the live deployed `AskHandler` after redeploying
all 5 stacks (`safe: true`, `intent: trend_check`, a grounded answer
explicitly citing the checked prior panel).

---

## 2026-09-05 — Phase 6 Workbench: minimal React/Vite frontend, scoped to `/ask` first; required adding CORS

**Context**: Every phase through the stress-test pass and both
independent-review rounds was backend-only -- real Cognito auth existed,
but the only callers were terminal tooling (`curl`, `pytest`,
`get_dev_token.py`). The Azure counterpart already has a React/Vite
Workbench; building the AWS equivalent is the natural next comparison
point (same API, different cloud's auth/client story), and it also turns
this project's own login/ask/trace-inspection loop into something usable
by a person, not just provable via a terminal.

**Decision**: Scoped the first version tightly rather than building the
whole API surface at once: real Authorization Code + PKCE through an
in-browser redirect to the Hosted UI (not a script standing in for one),
`POST /ask` only (not the async `/runs`/`/jobs` paths yet), and a full
render of the answer plus its grounding trace (safety checks, grounded
facts, limitations, sources). Plain React + Vite + TypeScript, no router
library (one `pathname === "/callback"` check covers the only extra
route this needs), no state-management or component library -- matching
the kernel/infra's own dependency discipline. Runs as a local dev server
on a fixed port (8765) that exactly matches the one redirect URI already
registered on the Cognito App Client, so no App Client change was needed.

**A real infra gap this surfaced**: API Gateway had never had a browser
caller before, so `ApiStack`'s `HttpApi` had no CORS configuration at
all -- every prior caller (curl, pytest, boto3) is same-origin-exempt by
construction. Added `cors_preflight` scoped to exactly
`http://localhost:8765` (not a wildcard), since that's the only origin
that's real right now; will need widening once a real hosted Workbench
URL exists. New test: `test_stacks.py::test_http_api_has_cors_scoped_to_the_workbench_dev_origin_not_a_wildcard`.

**A real bug this surfaced, live, that no unit test would have caught**:
React 18's `<StrictMode>` deliberately double-invokes effects in
development specifically to catch exactly this class of bug -- the
`/callback` route's `useEffect` called `completeSignIn(code)` twice on
the same mount, and an OAuth authorization code is single-use, so the
second exchange failed with a real `400 invalid_grant` from Cognito.
Harmless in this instance (the first exchange had already stored the
token before the second one's failure was handled), but a genuine race,
not a false alarm -- confirmed by checking the console log's full
history: exactly one `400`, timestamped before the fix's hot-reload, none
after across multiple subsequent sign-ins. Fixed with a `useRef` mount
guard, the standard pattern for a legitimate one-time side effect under
StrictMode.

**Verification**: `tsc -b` and `eslint .` both clean. `npm run build`
succeeds (150KB JS, gzipped ~49KB). Full live end-to-end test in a real
browser: sign-in through the actual Cognito Hosted UI (a human completed
the credential entry, per this project's standing constraint that
interactive Cognito login can't be automated), a real `/ask` call
answered by the deployed Bedrock-backed Lambda, `safe: true`, all 4
safety checks shown passing, 12 grounded facts rendered with their
sources -- and the fix re-verified across two additional sign-in/sign-out
cycles with zero new console errors. Infra regression suite (130 tests,
up from 129) and `cdk synth --all` both still pass with the CORS addition
in place; `CareAgentApiStack` redeployed live.

**Consequence**: The async paths, a run-history view, markdown rendering
for LLM-narrated answers (Bedrock's prose includes literal `**bold**`
markers, currently shown as-is), and real hosting (S3+CloudFront, needing
a second registered Cognito callback URL) are explicitly not done --
tracked in `docs/AWS_ROADMAP.md`'s Phase 6 section as open, not silently
implied to be finished.

---

## 2026-09-05 — A second independent review, scoped to *verify* round-1's fixes, found real regressions in them; fixed

**Context**: After the first independent review's 13 findings were fixed
(see the entries below), a second independent review was deliberately
scoped as a verification pass rather than a from-scratch re-scan: for
each "fixed" finding, does the fix actually close the gap, or only the
specific reproduction originally reported -- and did fixing it introduce
a new problem? Every claim below was independently reproduced against
this repo's own code before being trusted, the same standard applied
throughout this project.

**What it found, confirmed real**:

1. **A regression that broke ordinary, previously-safe answers.**
   Finding #4's value+unit binding fix (see the numeric-grounding entry
   below) required a `GroundedFact.unit` to be populated for the strict
   check to apply -- but the trend intent's `latest value`/`previous
   value` facts were never given one, even though `trend.py` already
   computed it. Reproduced live against this project's own sample data at
   commit `3985ce4`: `Is my LDL getting worse?`, `Is my HbA1c getting
   worse?`, and `What is my eGFR?` all returned `safe=False`, each for a
   different reason under the same root cause (a value's only grounding
   source lacked a unit) plus a second bug (the eGFR unit string
   `mL/min/1.73m2` contains its own digits, which the fallback bare-number
   scan re-discovered as a second, unrelated "ungrounded number" because
   only the *value*'s span, not the full value+unit match, was excluded
   from that scan).
2. **A synchronous run's cancellation could be silently undone.** The
   ownership+status condition added for finding #1 never excluded
   `execution_type = "SYNC"`, so a `/ask` run in flight could be marked
   `CANCELLED` -- and then have that overwritten back to `SUCCEEDED`/
   `FAILED` the moment `adapter.py`'s own unconditional terminal write
   landed, since there was never an execution to actually stop.
3. **A new IAM gap in code added to close finding #13.** `start_run.py`'s
   `ExecutionAlreadyExists` handling calls `DescribeExecution` to compare
   the existing run's real input, but `StartRunHandler`'s role only ever
   had `StartExecution`. This would `AccessDenied` on every duplicate
   `run_id` submission against the real account -- invisible to
   moto-mocked tests, which don't enforce IAM.
4. **The finding-#3 compensating write could clobber a real outcome.** An
   SDK exception from `send_message` doesn't prove SQS rejected the
   message -- it can mean the send succeeded and only the *response* was
   lost, in which case a consumer could already be processing or have
   finished the job. The unconditional compensating write would clobber
   that back to `FAILED`.
5. **A new inconsistency in `adapter.py`'s write order.** The DynamoDB
   record was marked `SUCCEEDED` before the S3 evidence write, with no
   handling if that write then failed -- leaving a record permanently
   claiming success with no evidence, and (because the conditional-create
   guard added for finding #2 now blocks it) not retryable under the same
   `run_id`.
6. **Finding #4's fix incompletely closes the original gap.** The
   value+unit check verifies *some* fact carries that exact pair, not
   that the text's claimed marker is the one that actually has it -- two
   markers sharing a unit (LDL/HDL/triglycerides/total cholesterol are
   all `mg/dL`) can still be swapped without detection.
7. **Finding #5's fix was incomplete.** It corrected the pacing/nutrition
   modifiers' `GroundedFact.claim` (metadata) but left `text` -- what the
   narrator actually renders -- still unconditionally naming both
   signals. The existing regression test for this only asserted on
   `claim`, not `text`, so it passed despite the bug. Separately, the
   medication/allergy cautions treated a bare substring match as positive
   evidence, so a denial ("Patient denies levothyroxine use") still
   produced an affirmative claim.
8. **The same bug pattern as finding #5, in two modifiers round 1 didn't
   touch.** `exercise_limitation` and `family_history_context` hardcoded
   a specific claim regardless of what the caution's own `detail` said.
9. **Finding #2's conditional-write fix doesn't cover overlapping
   deliveries or reconciliation.** `RUNNING -> RUNNING` is still allowed,
   so two concurrent SQS deliveries can both invoke the agent; a record
   stuck `RUNNING` after repeated consumer failures has no reconciliation
   against the DLQ.
10. **The stress-harness unification (see the entry below) was itself
    incomplete.** Both async success checks still accepted
    `status == "SUCCEEDED"` alone, without also requiring `safe is True`
    -- disagreeing with the sync path, which already required both.
11. **`run_id_validation.py` missed some of AWS's own documented invalid
    characters**: the surrogate range and the two Unicode noncharacters,
    reachable via a JSON body's `\uXXXX` escapes.
12. **Finding #11's IAM narrowing was itself incomplete.**
    `grant_write_data` still includes `DeleteItem`/`BatchWriteItem`,
    unused by `adapter.py`; the same over-grant pattern was untouched on
    every other DynamoDB-writing handler.
13. **Several documentation claims overstated the post-fix state**:
    `README.md`'s "provably grounded" and "never echoes the question"
    (true for the deterministic narrator's own output construction, not a
    structural guarantee about what reaches an LLM narrator as input,
    since `llm_narrator.py` puts the raw question directly into the
    prompt); a few remaining "guaranteed eventual success" phrases the
    original correction missed; and a CloudWatch `Invocations` count
    presented as proof of Lambda origin when that metric is model-level,
    not caller-level.

**Decision**: Fixed all of 1-5 (the regressions), 7, 8, 10, 11, 12, and 13
directly, each with a new or extended regression test reproducing the
specific failure mode. Did **not** attempt 6 (cross-marker value/unit
binding) or 9 (a processing lease + DLQ reconciliation) in this pass --
both need a real design change (structured claim rendering; a reclaimable
lease with attempt ownership), not a quick patch, and forcing one in
without the same care given to the rest of this project's architecture
would risk the same class of regression this whole review cycle just
caught. Documented both as open backlog items in
`docs/INDEPENDENT_REVIEW_FINDINGS.md`, the same way finding #3's
crash-between-calls gap already was, rather than silently left unrecorded.

**A note on how #8 was fixed, since it introduced its own near-miss**:
rendering the caution's raw `detail` text directly (instead of an assumed
specific claim) means any number literally present in that text --
e.g. the "2" in "type 2 diabetes" -- now appears in the visible answer.
The first version of this fix broke `test_agent_edge_cases.py` and
`test_agent_main_question.py` because that "2" wasn't registered as a
grounded value and `verify_numeric_grounding` correctly flagged it as
ungrounded. Fixed by extracting any numbers present in the caution's own
`detail` into the corresponding `GroundedFact.numeric_values` -- they're
sourced from real reported data, not narrator invention, so they should
be grounded, just like any other sourced value. Noted here because it's
exactly the kind of self-inflicted regression this whole review cycle is
about, caught by running the full test suite before considering the fix
done rather than only the specific new test written for it.

**Verification**: Full kernel suite (141 tests, up from 135) and full
infra suite (129 tests, up from 125) both pass; `ruff`/`ruff format`/
`mypy` clean on both; `cdk synth --all` succeeds with the new IAM grants.
The three originally-failing live questions (LDL trend, HbA1c trend,
eGFR) were re-run against the real pipeline after the fix and now return
`safe=True`. The `states:DescribeExecution` grant and the per-handler IAM
narrowing were verified against the real synthesized CloudFormation
template, not assumed from the CDK call alone. No new `cdk deploy` was
made in this pass -- unlike the two rounds before it, these fixes were
verified against synthesized templates and moto-mocked/kernel tests, not
re-deployed and re-exercised against the live account. That's a smaller
verification bar than the first two rounds held themselves to, disclosed
here rather than implied otherwise.

---

## 2026-09-05 — `stress_test.py`'s success definition was inconsistent across commands; unified

**Context**: An independent review pointed out that `burst-async`'s
success check (`desc["status"] == "SUCCEEDED"`, the raw Step Functions
execution status) can disagree with what actually happened to the agent
run. `RecordFailure`/`RecordTimeout` are both plain `End: true` states
reached via `InvokeAgent`'s `Catch` branch, not an unhandled execution
error -- so a run where the agent genuinely failed, and the state machine
correctly caught and recorded that failure, still reports its own
top-level execution status as `SUCCEEDED` (exactly right for "did the
*workflow* complete as designed," wrong for "did the *agent's answer*
succeed," which is what this harness's `ok` field is supposed to mean).
`burst-queue` already checked the DynamoDB application-level status
instead -- meaning the two async paths weren't even measuring the same
thing when compared against each other in `docs/STRESS_TEST.md`.
Separately, `burst-sync`/`adversarial` (`_invoke_ask_handler`) counted
HTTP 200 alone as success, not also requiring `safe: true` from the
response body.

**Decision**: `_start_and_poll_execution` (`burst-async`) now polls the
DynamoDB record directly, the same approach `_enqueue_and_poll`
(`burst-queue`) already used, so both async paths share one definition.
`_invoke_ask_handler` (`burst-sync`, `adversarial`) now requires
`status == 200 and safe is True`. Did not extend this to a full
transport/workflow/application/safety-level breakdown in the reporting
(the review's fuller suggestion) -- unifying the single `ok` definition
across commands was the load-bearing gap; splitting it into multiple
reported dimensions is a further, separate improvement not done here.

**Verification**: re-ran `burst-async -n 5` live against the real
deployed state machine after the fix -- 5/5, with the harness's own
output now correctly showing the DynamoDB-sourced `narrator_backend`
(`"bedrock"`) and `safe` (`true`) fields, which the old
execution-status-only check never surfaced at all. Checked whether this
retroactively changes any previously-published `STRESS_TEST.md` numbers:
no -- every failure observed in that pass was `Lambda.TooManyRequestsException`
at the `MarkRunning` step, which has no `Catch` and so fails the
*execution* outright (not caught and gracefully recorded) -- meaning the
old and new checks agree for every run actually measured. The bug was
real, but it happened not to distort the specific numbers already
published; it would have mattered for any run where the agent's own
logic (not Lambda-service throttling) was what failed.

---

## 2026-09-05 — Switched `get_dev_token.py` from the ID token to the access token; the original ADR's technical claim was wrong

**Context**: An earlier decision (below, "App Client is a public client...
ID token not access token") chose the ID token specifically because
"the access token carries a `client_id` claim instead and isn't the
conventional shape for this check." An independent review flagged this
as factually incorrect: API Gateway's HTTP API JWT authorizer checks the
configured audience list against the token's `aud` claim when present,
and automatically falls back to checking `client_id` when it isn't --
exactly the shape a Cognito access token has. This is documented AWS
behavior, not an assumption. Access tokens (meant to carry scopes
authorizing API calls) are also the more conventional OAuth2 choice for
this purpose than ID tokens (meant to represent user identity to the
client application that requested them, not to authorize a downstream
API) -- the original choice was backwards from OAuth2 convention on top
of resting on an incorrect technical premise.

**Decision**: `get_dev_token.py` now exports `CARE_AGENT_ACCESS_TOKEN`
(the OAuth2 access token) instead of `CARE_AGENT_ID_TOKEN`. **No change
was needed to `api_stack.py`'s `HttpJwtAuthorizer` configuration** --
`jwt_audience=[app_client.user_pool_client_id]` already works for both
token shapes, since the authorizer itself handles the `aud`-vs-`client_id`
fallback. This is a real example of a fix that turned out to be far
smaller in scope than the finding suggested: correcting the actual
premise showed the "bug" was entirely in which token a client chose to
send, not in how the deployed infrastructure validates it.

**Not done**: this switch does not add per-route OAuth scopes (e.g. a
custom Cognito resource server with `runs.read`/`runs.write`-style
scopes, enforced via `authorizationScopes` on each HTTP API route). The
independent review's finding was specifically about the *token type*
being suboptimal, not about the *absence* of scope-based route
authorization -- today, any successfully authenticated caller (regardless
of token type) can call any route; ownership is enforced at the
application/data layer (`owner_sub`, see the authorization-vulnerability
fix elsewhere in this log), not via OAuth scopes. Adding real per-route
scopes would be a legitimate further improvement, not done here.

**Verification**: `tests/test_get_dev_token.py` (PKCE math, URL/request
construction) is unaffected -- it never touched the token-exchange
response shape. `infra/tests/test_live_endpoint_smoke.py` renamed its
env var accordingly. The actual live login flow (an interactive browser
step through the real Cognito Hosted UI) needs to be re-run once by
whoever next uses `get_dev_token.py` to confirm the resulting access
token is accepted end-to-end against the deployed API -- that step needs
a human at a real browser and wasn't done as part of this fix.

---

## 2026-09-05 — Independent review found a real authorization vulnerability and a run-record data-integrity bug; fixed both

**Context**: Per the challenge brief's own process checklist ("get a second,
independent AI session to critically review the phase"), an independent
model reviewed this repository's code (not just its docs) with explicit
instructions to verify claims against implementation rather than take the
documentation at face value. It found, and reproduced, two High-severity
issues -- verified directly against this repo's own code before accepting
either as real (see the specific verification steps below, not just
"the reviewer said so"). Full findings list, including the ones not
addressed here: `docs/INDEPENDENT_REVIEW_FINDINGS.md`.

**Finding #1 -- authentication existed, authorization didn't.** API
Gateway's Cognito JWT authorizer (Phase 2) validates that a request
carries a genuine, unexpired token -- but nothing downstream of that ever
checked *which* run records the token's holder was entitled to touch.
`get_run.py` and `cancel_run.py` looked up/mutated records by `run_id`
alone; any authenticated caller who knew or guessed a `run_id` could read
or cancel any other caller's run. Verified directly: read both handlers'
source, confirmed neither referenced the request's JWT claims at all.

**Finding #2 -- three paths sharing one run_id keyspace, zero conditional
writes outside the Step Functions leaf.** `/ask` (`adapter.py`), `/runs`
(`mark_running.py`), and `/jobs` (`enqueue_job.py`/`process_job.py`) all
key DynamoDB by `run_id`, but only `record_result.py`'s terminal write was
ever conditional. `adapter.py`'s `put_item` was a full, unconditional
overwrite -- a `/ask` call reusing another path's `run_id` would silently
erase that record's `status` entirely (`put_item` replaces the whole item,
it doesn't merge fields). `process_job.py`'s writes were unconditional
too: SQS's at-least-once delivery means a redelivered message (the first
attempt's Lambda timed out, or its ack didn't land before the visibility
timeout) could set an already-`SUCCEEDED` record back to `RUNNING` and
re-run the agent, and `cancel_run.py` marking a queued job `CANCELLED`
could get silently overwritten the moment `process_job.py`'s own write
landed, since neither side checked the other. Verified directly: traced
every DynamoDB write across `adapter.py`, `mark_running.py`,
`enqueue_job.py`, and `process_job.py`; none but `record_result.py` used
`ConditionExpression`.

**Decision**: Redesigned the run record's write contract instead of
patching each symptom individually:

- Every record now carries `owner_sub` (the creating caller's Cognito
  `sub` -- the actual authorization principal; `user_id`, by contrast, is
  a caller-supplied field naming whose *synthetic health-data profile* a
  question is about, and was never itself proof of identity) and
  `execution_type` (`SYNC` / `STEP_FUNCTIONS` / `SQS`).
- New `auth_context.py` extracts `owner_sub` from
  `event.requestContext.authorizer.jwt.claims.sub` -- the claims HTTP
  API's JWT authorizer attaches to the event, already present on every
  route (all of them require the authorizer), just never read by any
  handler.
- `get_run.py` now returns 404 -- not the data, not a 403 -- for a
  non-owner. 404 rather than 403 is deliberate: confirming "this run
  exists but isn't yours" is itself information a non-owner shouldn't be
  able to learn.
- `cancel_run.py`'s conditional update now folds the ownership check
  *into the same atomic `ConditionExpression`* as the status check
  (`owner_sub = :owner_sub AND (#status = :queued OR #status = :running)`)
  rather than a separate get-then-act -- closing the TOCTOU race a
  separate check would leave open. It also now only attempts
  `stop_execution` when `execution_type == "STEP_FUNCTIONS"` (read back
  via `ReturnValues=ALL_NEW` on the same call, no extra read) -- it used
  to call `stop_execution` unconditionally and silently swallow the
  resulting `ExecutionDoesNotExist` for an SQS-queued job via a bare
  `except ClientError: pass`, reporting success while `process_job.py`
  was about to overwrite the "cancelled" record anyway.
- `adapter.py`, `mark_running.py`, and `enqueue_job.py` now create their
  record with `ConditionExpression: attribute_not_exists(run_id)` --
  cross-path `run_id` collision now returns 409 instead of silently
  replacing another path's record. `adapter.py` also now transitions
  through `RUNNING` -> `SUCCEEDED`/`FAILED` instead of writing nothing
  until the very end, so a request that fails partway doesn't leave no
  record at all.
- `process_job.py`'s writes are now conditional on the record's *current*
  status: the initial `RUNNING` write only proceeds from `QUEUED` or
  `RUNNING` (idempotent under redelivery, but refuses to reopen a
  terminal record); the final write only proceeds from `RUNNING`
  (mirroring `record_result.py`'s existing, already-correct pattern). A
  failed condition is treated as "something else already finalized this
  run" -- not an error, and specifically not re-raised, since re-raising
  would just cost the queue another wasted redelivery attempt on work
  that's already settled.

**Verification**: reproduced both original bugs against this repo's own
code before fixing them (a fabricated second-caller JWT could read/cancel
a first caller's run; a simulated cancel-during-processing race left a
`SUCCEEDED` overwrite), then added regression tests that reproduce the
exact same scenarios and assert the fix holds --
`test_get_run_owned_by_another_caller_returns_404_not_403`,
`test_cancel_run_owned_by_another_caller_returns_404_not_the_real_status`,
`test_cancel_run_cancels_a_queued_sqs_job_without_attempting_stop_execution`,
`test_mark_running_refuses_to_overwrite_a_run_id_collision`,
`test_enqueue_refuses_to_overwrite_a_run_id_collision`,
`test_process_job_does_not_reopen_or_reprocess_an_already_cancelled_job`,
`test_process_job_final_write_does_not_clobber_a_cancellation_that_raced_in_mid_processing`.
Full kernel + infra suites re-run clean after the change (see the
adjacent commit).

**Consequence**: This is the single most important fix this project has
made outside of getting a feature working -- an authorization gap in a
health-data application, even over synthetic data, directly contradicts
the project's own stated safety-first framing, and it existed through
four completed phases and a full stress-testing pass without being
caught, because none of that testing ever asked "what happens if a
*different* authenticated caller tries this." That question is exactly
what an independent second reviewer is for.

---

## 2026-09-05 — Independent review found the numeric-grounding and diagnosis/dosing safety checks had real, exploitable gaps; tightened all four

**Context**: Same independent review as above. It constructed six
narrator outputs and ran them through `safety.run_safety_checks` directly
-- not claiming a real Bedrock call produced them, just showing the
*validators themselves* would pass each one as `safe=True`. All six were
reproduced against this repo's actual code before any fix:

| Probe | What it proved |
|---|---|
| `"Your HbA1c is 162%."` | `verify_numeric_grounding` only checked "does this number appear *somewhere* in the grounded facts," never *which marker* it's attached to -- a real, correctly-grounded LDL-C value (162 mg/dL) could be reattached to a completely different marker and unit. |
| `"Your LDL-C is 5 mg/dL."` | `allowed_extra_numbers` (the ordinal-list-marker exemption, hardcoded to `{1.0, ..., 5.0}`) exempted those *values* anywhere in the text, not just at the position they're actually safe (a line-leading "5. " list marker) -- so a fabricated value happening to equal a valid list-numbering value passed everywhere. |
| `"Your LDL-C is 999mg/dL."` | `_NUMBER_RE`'s trailing `(?![\w.])` blocked matching a number immediately followed by a letter -- any fabricated value could escape numeric extraction *entirely* just by omitting the space before its unit. |
| `"Diabetes is your confirmed condition."` | `_DIAGNOSIS_PATTERNS` only matched "you have/are diagnosed with X" phrasings, not "X is your condition." |
| `"Swallow one vitamin D capsule every morning."` | `_DOSING_PATTERNS` required a digit (mg/mcg/IU/etc.); a written-word dosing/frequency instruction with no digits at all matched none of them. |
| `""` (empty string) | No check verified the answer contained anything -- an empty answer trivially passes every check (no diagnosis pattern matches nothing, no number to be ungrounded). |

**Decision**: Rewrote `safety.py`'s numeric-grounding check around a new
capability rather than only patching each regex individually:

- `GroundedFact` gained an optional `unit` field (`models.py`), populated
  *directly from the source biomarker's own `unit` field* at construction
  time in `agent.py` -- not parsed back out of the `claim` string's free
  text, which would just move the fragility rather than remove it.
- `verify_numeric_grounding` now checks (value, unit) pairs for any
  number with a recognized unit immediately adjacent (`_KNOWN_UNITS` --
  the *actual, complete* unit vocabulary this project's sample data uses,
  verified by scanning every marker in `data/sample_bloodwork.json`, not
  guessed): the number must match a grounded fact carrying that *same*
  unit, not just the same value attached to any marker. A number with no
  recognized unit adjacent (or one using a unit this project doesn't yet
  recognize) still falls back to the older, weaker value-only check --
  there's no way to bind context that isn't there.
- The ordinal-list-marker exemption is no longer a value-based allowlist.
  It's now a position-based one: only the exact character span of a
  line-leading `N. ` marker is exempted, via `_ORDINAL_LIST_MARKER_RE`,
  not the numeral's value anywhere else in the text. `_ORDINAL_NUMBERS`
  and the `allowed_extra_numbers` parameter it fed were removed from
  `agent.py`/`safety.py` entirely -- the position-based mechanism doesn't
  need a caller-supplied allowlist at all.
- `_NUMBER_RE`'s trailing lookahead was removed (the leading
  `(?<![\w.])` -- which correctly excludes digits embedded in identifiers
  like `kb_a1c_006` -- was kept). A number glued to its unit with no space
  is now extracted correctly either way.
- `_DIAGNOSIS_PATTERNS` and `_DOSING_PATTERNS` were both expanded to catch
  the specific phrasings above plus direct siblings (`"your condition
  is X"`, `swallow/take one <capsule/tablet/pill/...>`, a dosage-form word
  within ~40 characters of a frequency word like "every morning").
- Added a fourth check, `check_non_empty`, run as part of
  `run_safety_checks`.

**What this does *not* claim** (see `safety.py`'s own module docstring,
rewritten to say this explicitly): checks 2 and 3 remain pattern-based
over English phrasing and cannot be made complete against a sufficiently
creative paraphrase -- expanding pattern coverage raises the bar, it
doesn't close the class of bypass. Check 4's value+unit binding only
applies to units in `_KNOWN_UNITS`; a number attached to an unrecognized
unit spelling still only gets the weaker check. None of this replaces
`agent.py`'s existing fallback-to-mock-narrator behavior on any check
failure, which remains the actual safety net -- these checks are what
that fallback depends on being accurate.

**Verification**: all six original probes re-run against this repo's
actual pipeline (`HealthAgent.ask()`'s real grounded facts, not
hand-constructed fixtures) after the fix -- all six now correctly fail
the check that used to let them through. 8 new regression tests in
`tests/test_safety.py`, one per probe plus the two regex bugs the fix
itself introduced and caught before committing (an ordinal-marker span
mismatch between the exemption regex and `_NUMBER_RE`'s own match
boundaries, and a `\b`-after-`%`-before-punctuation edge case in the new
value-unit regex -- both caught by running the new tests, not assumed
correct on the first attempt). Full kernel suite (132 tests) and
`care-agent eval-samples` re-run clean after the change.

**Consequence**: This is the second-most-important fix from the same
review, and arguably the more sobering one: these checks are the
project's stated reason an LLM narrator is safe to use at all
("`kb_grounding_002`... is what makes an optional LLM narration pass safe
to use"), and every prior phase's real-Bedrock evidence
(`docs/PHASE4_BEDROCK_EVIDENCE.md`) happened to never trigger any of
these six specific gaps -- not because the gaps weren't there, but
because the real model's actual phrasing choices didn't happen to hit
them in the handful of live calls made. Passing live evidence and passing
an adversarial review are different bars; this project had only cleared
the first one.

---

## 2026-09-05 — Two previously-published stress-test claims were wrong; corrected

**Context**: Same independent review. Two of its findings weren't about
the application's behavior at all, but about whether this project's own
*measurements* of that behavior were trustworthy.

**#6 -- `stress_test.py --no-retry` didn't actually disable retries.**
`Config(retries={"max_attempts": 1})` was meant to show what a real API
Gateway caller (no SDK retry safety net) experiences under Lambda
throttling. Checked directly against `client.meta.config.retries`: it
resolves to `total_max_attempts: 2` -- one retry still happens, despite
the name. `total_max_attempts=1` (with `mode="standard"`) is the actual
zero-retry setting, confirmed the same way. **Consequence**: the
originally published `docs/STRESS_TEST.md` "burst-sync, SDK retry
disabled: 10/15 ok" result was measured with one retry still active, not
zero. Fixed the harness and re-ran the affected comparison -- see
`docs/STRESS_TEST.md` for the corrected number.

**#9 -- the "3 retry attempts" description of the Step Functions retry
policy was incomplete.** Synthesizing `OrchestrationStack`'s actual ASL
showed CDK inserts its *own* default retry policy
(`Lambda.ClientExecutionTimeoutException`/`ServiceException`/
`AWSLambdaException`/`SdkClientException`, 6 attempts) onto every
`LambdaInvoke` task automatically, ahead of the custom 3-attempt policy
this project added -- something the code never disabled and the docs
never mentioned. Step Functions resolves overlapping `Retry` entries by
taking the *first* one in the array whose `ErrorEquals` list contains the
specific error that occurred, not by summing or always using the first
entry -- so for `Lambda.TooManyRequestsException` specifically (the only
error type actually observed throughout this project's stress testing,
and the only one of the four error codes *not* also in CDK's default
policy), the custom 3-attempt policy is genuinely what governed, and the
previously-published throttling numbers are unaffected. But the blanket
claim that every `LambdaInvoke` task retries "3 times" was inaccurate for
the other three error codes, which would get CDK's 6-attempt default
instead. Corrected `orchestration_stack.py`'s module docstring and
`docs/STRESS_TEST.md`/`AWS_ROADMAP.md`'s phrasing to describe both
policies rather than only the one this project added on purpose.

**Consequence**: Neither of these changes the conclusions already drawn
(the sync path still has meaningfully less resilience than the async
paths; the async retry fix from the earlier stress-test still measurably
helped) -- but both are a reminder that a stress-testing tool's own
configuration is exactly as susceptible to being wrong as the thing it's
testing, and deserves the same "verify, don't assume" treatment.

---

## 2026-09-04 — Added an SQS-buffered path as a direct, load-tested comparison against Step Functions retry

**Context**: After the concurrency stress test found Step Functions'
bounded retry degrading under enough sustained load (41/50 succeeding at
a 50-concurrent burst -- see `docs/STRESS_TEST.md`), the user asked
specifically whether adding a real SQS-buffered path -- matching the
pattern the Azure/Durable-Functions counterpart uses internally -- would
improve on that, and asked for it to be built and load-tested, not just
discussed.

**Decision**: Built `QueueStack` (`infra/stacks/queue_stack.py`) as a
genuinely separate, deployed third path, not a modification of the
existing two: `POST /jobs` (`enqueue_job.py`) writes a `QUEUED` record and
sends one SQS message, returning 202 immediately; an SQS-triggered Lambda
(`process_job.py`) consumes messages with the event source's
`max_concurrency=5` -- a hard cap on concurrent consumer Lambdas
*regardless of queue depth*, which is the mechanism this whole comparison
is about. A dead-letter queue (`maxReceiveCount=3`) catches genuinely
un-processable messages. Deliberately reused the existing `RunsTable` and
`GET /runs/{run_id}` (`get_run.py`, already schema-agnostic) for polling
rather than adding a new table or endpoint -- the comparison is about the
ingestion/processing mechanism, not about needing a parallel data model.

**Why `max_concurrency=5`, not higher**: the account's real Lambda
concurrency ceiling is 10, shared across every function. Setting the
queue's consumer cap to half of that leaves headroom for every other
Lambda in the account (the sync path, the Step Functions path, the
enqueue Lambda itself) to keep functioning normally while the queue is
actively draining a burst -- capping it at or near 10 would let a large
queue burst starve the rest of the system, defeating part of the point of
buffering in the first place.

**Verification**: ran the identical burst sizes used for the Step
Functions comparison (15, 50) plus a further push to 100 (2x the size
that made Step Functions degrade) against the real deployed queue.
Result: **100% success at every size tested, including 100 concurrent
(10x the account's raw Lambda ceiling)**, at the cost of latency scaling
roughly linearly with burst size (p50 ~13s at 15 concurrent -> ~60s at
100 concurrent) -- exactly the trade-off basic queueing theory predicts
for a fixed-concurrency consumer. `JobsDLQ` stayed empty at every size,
confirmed via `aws sqs get-queue-attributes`, not assumed. Full numbers
and the head-to-head table: `docs/STRESS_TEST.md`.

**Consequence, and the actual comparison point**: this is not "SQS is
better" -- it's a genuine trade-off, now quantified instead of asserted.
Step Functions' retry is faster in the common case and simpler (no extra
queue resource, no DLQ to monitor) but has a bounded retry budget that a
large enough burst can exhaust. SQS buffering has no such ceiling on
eventual success but makes callers wait proportionally longer during a
real burst, and adds real operational surface (a queue + a DLQ to watch).
Which one is "right" depends entirely on whether the caller needs a
bounded-time answer or needs a guaranteed-eventual one -- the same
question the Azure/Durable-Functions comparison was gesturing at, now
answered with real numbers from both sides of this project rather than
architectural intuition alone.

---

## 2026-09-04 — `process_job.py`'s generic DynamoDB writer hit `status` being a reserved keyword

**Context**: While writing `process_job.py` (the SQS consumer, see the
entry above), its `_write_result(run_id, **fields)` helper built an
`UpdateExpression` directly from keyword-argument names (`f"{key} = :{key}"`)
for brevity, unlike `record_result.py`/`mark_running.py`/`cancel_run.py`,
which all hand-write `ExpressionAttributeNames` for `status` specifically.
The very first test run against moto failed with a real
`ValidationException: Attribute name is a reserved keyword; reserved
keyword: status` -- moto correctly reproduces this specific DynamoDB
behavior, so this would have failed identically against the real service.

**Decision**: Fixed by aliasing *every* field name through
`ExpressionAttributeNames` unconditionally (`f"#{key}"` for every key,
not just `status`), rather than special-casing the one reserved word
known today. DynamoDB has a long, non-obvious reserved-word list; a
generic helper that takes arbitrary field names should not have to be
kept in sync with that list by hand every time a new field gets added.

**Consequence**: A small, cheap catch from actually running the test
suite against moto (which models real DynamoDB validation behavior,
not just a generic key-value store) rather than only reasoning about the
code -- exactly the value moto's fidelity is supposed to provide, working
as intended here.

---

## 2026-09-04 — Stress test found two real bugs: truthiness-only input validation, and retry only wired onto one of four Lambda tasks

**Context**: With Phase 4 closed (Bedrock genuinely running in the
deployed Lambdas), the user asked directly whether the runtime was now
"actually" cloud-native end to end, and separately whether it was worth
deliberately stress-testing it -- adversarial input, real concurrency,
robustness, persistence -- rather than assuming it would hold up because
it worked in ones-of-calls testing. Built
`infra/scripts/stress_test.py`, a live (not CI) harness with four
subcommands, and ran all four against the real deployed account. Full
methodology and numbers in `docs/STRESS_TEST.md`; this entry is just the
two bugs it found and why they were fixed the way they were.

**Bug 1 -- non-string `question`/`user_id`/`run_id` produced a 500 or an
uncaught error, not a 400**: `adapter.py` and `start_run.py` both
validated presence with `if not user_id or not question`, which is
*truthy*-only. A number, list, or dict all pass that check. Locally
probing `HealthAgent.ask()` directly with a non-string `question_text`
showed it raises an unhandled `AttributeError` deep inside intent
classification (`.lower()` on a non-`str`) -- in `adapter.py` this was
caught by a broad `except Exception` and turned into a 500 that leaked
the raw Python exception message to the caller; in `start_run.py` it was
worse, since a non-string `run_id` reaches `start_execution(name=run_id,
...)` (which requires a string) with no validation and no catch beyond
`ExecutionAlreadyExists`, surfacing as a raw, uncaught boto3
`ClientError`.

**Decision**: Added an explicit `isinstance(..., str)` check alongside
the existing truthiness check, in both handlers, for all three fields.
Wrong type is the caller's mistake (400), not an internal failure (500) --
same reasoning already applied to the `isinstance(body, dict)` fix from
Phase 1's null-JSON-body bug. Did *not* add the same check inside
`agent_task.py` (the Step Functions task Lambda) -- its docstring already
documents a deliberate design choice to let exceptions propagate so Step
Functions' own `Catch` becomes the error boundary for that path; a
non-string `question` reaching it (which can now only happen via a
direct, bypassing-the-API invocation, not through `start_run.py` anymore)
still correctly lands the execution in `FAILED`, just not as cheaply as
rejecting it at the API boundary.

**Verification**: added 16 new tests across `test_adapter.py` (type
checks + a 7-case adversarial-input sweep: empty, whitespace, 50k chars,
multilingual Unicode, control characters, SQL-injection-shaped,
prompt-injection-shaped) and `test_orchestration_lambdas.py` (the
`start_run.py` equivalents); all run in CI going forward, all against the
mock narrator (free, deterministic -- the mock is template-based and
structurally can't be talked into anything, so these test input-handling
robustness, not LLM safety). Redeployed and re-verified live against the
actual deployed `AskHandler`: a real `aws lambda invoke` with
`question: 12345` now returns a clean 400 with no leaked exception text.

---

## 2026-09-04 — Step Functions retry was only wired onto InvokeAgent; a live burst test found the other three tasks equally exposed

**Context**: Part of the same stress-testing pass. Fired 15 concurrent
Step Functions executions at the deployed state machine (`stress_test.py
burst-async -n 15`) to compare the async path's resilience against the
synchronous `/ask` path under the same load. Expected the async path,
with Phase 3's "bounded retry" as one of its stated reliability
properties, to clearly outperform the sync path (which has no retry at
all). Instead: **10/15 succeeded, 5 failed -- the same failure count as
the unprotected sync path**, which defeated the point of having retry at
all.

**Investigation**: pulled the Step Functions execution history for one of
the 5 failures. It died at the *first* state, `MarkRunning`, within
~150ms of `ExecutionStarted` -- a single `TaskFailed`
(`Lambda.TooManyRequestsException`) immediately followed by
`ExecutionFailed`, with no retry attempt visible at all.
`orchestration_stack.py` only ever called `.add_retry(...)` on
`invoke_agent_task`; `mark_running_task`, `record_success_task`,
`record_failure_task`, and `record_timeout_task` had none. The account's
Lambda concurrency ceiling (10 -- see `docs/STRESS_TEST.md` for how this
was confirmed via `aws service-quotas`) is shared across every Lambda
function in the account, so a burst throttles whichever task happens to
be invoking at that moment with equal likelihood -- not just
`InvokeAgent`, which is the only one anyone had been watching.

**Decision**: Extracted the retry policy (3 attempts, 2s interval, 2x
backoff, the same `Lambda.*`/`TooManyRequestsException` error list
already used for `InvokeAgent`) into one shared
`_add_throttling_retry()` helper and applied it to *all four*
`LambdaInvoke` tasks in the state machine, not just `InvokeAgent`.
Considered widening `MarkRunning`'s retry errors to also cover generic
application exceptions, and rejected that -- retrying a genuine
application bug (as opposed to transient Lambda-service throttling) just
delays the same failure and can mask a real problem; the fix is scoped to
exactly the error class that caused this specific incident.

**Verification**: redeployed, re-ran the identical burst (n=15): 15/15
succeeded (up from 10/15), latency p95 dropped from 13.43s to 11.68s.
Pushed further to find where retry alone stops being enough: n=30 also
hit 15/15 -> 30/30 (100%), n=50 dropped to 41/50 (82%), with all 9
failures again `MarkRunning` exhausting its 3 retry attempts under
sustained load -- an honest capacity limit (retry smooths a burst, it
doesn't create capacity that isn't there), not a remaining bug. See
`docs/STRESS_TEST.md` for the full numbers and what a real fix past this
point would look like (a Lambda concurrency increase or an SQS buffer,
neither implemented here).

**Consequence**: This is the concrete, load-tested version of the
architectural claim Phase 3 made in the abstract ("orchestration buys
real resilience over a bare synchronous call") -- and finding it only
*half* true on the first real burst test is exactly the value of actually
running one instead of trusting the design read well. Also a legitimate
comparison point against the Azure/Durable-Functions side: whatever the
equivalent of "did we remember to apply the retry policy to every
activity, not just the one we were testing" turns out to be there.

---

## 2026-09-03 — Bedrock live call blocked by new-account verification, not by IAM or model access

**Context**: Phase 4's stated highest-priority goal was one real,
non-mocked Bedrock call as evidence -- explicitly because the Azure side
never got this far (`CannotDeployDueToLocalRegulations` on model
deployment, subscription-eligibility, never resolved). Attempted the AWS
equivalent: `aws bedrock-runtime converse` against
`anthropic.claude-haiku-4-5-20251001-v1:0`.

**What happened**: `AccessDeniedException: Your account is currently
being verified. Verification normally takes less than 2 hours.` Ruled out
the two more mundane explanations before accepting this at face value:
- **Not an IAM problem** -- `dev-cli` already has `AdministratorAccess`
  (confirmed earlier, Phase 1's bootstrap fix).
- **Not model-specific** -- tried a second, older Anthropic model
  (`claude-3-haiku-20240307-v1:0`) and got a *different* error entirely
  (`ResourceNotFoundException`, a Bedrock model-lifecycle/legacy-model
  restriction, unrelated to account verification). Two different models
  producing two different, unrelated errors rules out "this one model
  needs access requested" as the actual blocker for the first case.
- The AWS account itself (not just the IAM user) was created today, which
  matches AWS's own explanation: new accounts get a temporary anti-fraud
  hold on higher-risk, billable operations like Bedrock model invocation.

**Decision**: Don't chase this further right now -- it's a time-bound
hold, not a configuration problem to solve. Built and fully tested
`bedrock_narrator.py` regardless (mocked at the boto3-client boundary, so
none of that work depends on the account being unblocked). Left the "real
call" roadmap item explicitly open (⬜, not falsely marked done) rather
than treating the code being ready as equivalent to the capability gap
being closed.

**Consequence, and the actual cross-cloud comparison point**: both clouds
hit a real-account provisioning obstacle on their respective model layer,
but the *shape* of the obstacle differs in a way worth naming directly:
Azure's was a **support-ticket-bound eligibility/regulatory block** with
no stated resolution timeline (a case was opened, never resolved during
that project's timeframe); AWS's is a **self-service, time-bound identity
verification hold** with a stated expected resolution window from AWS's
own error message. Whether that difference holds up (i.e., whether this
actually clears in ~2 hours as claimed) is itself part of what this
comparison is for -- worth updating this entry once it's known either way,
rather than assuming the more optimistic framing is correct just because
it sounds better.

**Update (same day, resolved)**: the hold cleared in well under the stated
window -- confirmed by retrying the exact same `aws bedrock-runtime
converse` call from the previous entry with no other change, and it
succeeded. AWS's "self-service, time-bound" framing held up in practice,
unlike Azure's open-ended support-ticket block. See the next two entries
for what came up immediately after the hold cleared.

---

## 2026-09-03 — Bedrock real call needs a cross-region inference profile ID, not the bare model ID

**Context**: Once the account-verification hold cleared, the first real
`converse` call against the bare on-demand model ID
(`anthropic.claude-haiku-4-5-20251001-v1:0`) still failed, with a
different, unrelated error: `ValidationException: Invocation of model ID
anthropic.claude-haiku-4-5-20251001-v1:0 with on-demand throughput isn't
supported.`

**Decision**: Newer Anthropic models on Bedrock are only invocable through
a cross-region inference profile ID (the `us.` prefix), not the bare
on-demand model ID. Switched `BedrockNarrator`'s `DEFAULT_MODEL_ID` to
`us.anthropic.claude-haiku-4-5-20251001-v1:0` and confirmed the same call
succeeds with no other change. Left `BEDROCK_MODEL_ID` overridable via env
var (already was) with a code comment explaining *why* the default carries
the `us.` prefix, so a future model swap doesn't silently reintroduce this
error.

**Consequence**: This is an AWS-specific gotcha with no Azure-side
equivalent surfaced yet (the Azure project never got a real model call
working at all) -- worth keeping as a concrete example of an
undocumented-until-you-hit-it platform quirk for the eventual
cross-cloud writeup.

---

## 2026-09-03 — Live Bedrock output failed `numeric_grounding` on natural-language dates; fixed in the shared system prompt

**Context**: With the inference-profile fix in place, `care-agent
eval-samples --narrator-backend bedrock` ran against real Bedrock for all
three sample questions. `q_missing_context`'s answer silently fell back to
the mock narrator (`narrator_fallback` present in the trace) instead of
using the real Claude Haiku 4.5 output.

**What happened**: Claude Haiku wrote source dates in prose form ("May 6,
2026") instead of the ISO format the grounded facts use
(`2026-05-06`). `safety.py`'s `verify_numeric_grounding` only recognizes
ISO-format dates as a single grounded unit (`_ISO_DATE_RE`); a
prose-rendered date's individual number tokens (`2026`, `6`, `8`) got
checked as standalone numeric claims, didn't match any grounded value
verbatim, and the whole response was correctly rejected as
possibly-ungrounded -- exactly the safety net Phase 4 set out to test,
working as designed against a real model's actual output style, not a
contrived case.

**Decision**: Fixed at the prompt layer, not the safety-check layer:
added one instruction to the shared `SYSTEM_PROMPT`
(`src/care_agent/narrator/_prompt.py`, used by every LLM-backed narrator)
telling the model to keep dates in the exact `YYYY-MM-DD` format from the
source facts rather than writing them out in words. Did not loosen
`verify_numeric_grounding` to also parse prose dates -- the check doing
its job correctly (rejecting a plausible-looking but unverifiable
rewording) is the behavior worth keeping; the fix belongs on the output
side that can be steered, not on relaxing what "grounded" means.

**Verification**: re-ran the same question after the prompt change --
real Bedrock output now uses `2026-05-06` verbatim, `narrator_backend:
"bedrock"` with no `narrator_fallback` entry, all three safety checks
(`no_diagnosis`, `no_dosing`, `numeric_grounding`) pass. Re-ran the full
mocked test suite (125 passed) and all three `eval-samples` questions
live against real Bedrock afterward to confirm the fix generalized past
the one question that surfaced it, not just patched over a single
example. See `docs/PHASE4_BEDROCK_EVIDENCE.md` for the full real
output/trace.

**Consequence**: A second concrete, non-obvious finding from the one
Phase 4 explicitly prioritized "make at least one real call" for -- this
kind of format-drift-vs-grounding-check interaction is exactly the sort
of thing that never shows up against a hand-written mock response and
only surfaces against a real model.

---

## 2026-09-04 — Bedrock wired into the deployed Lambdas, with IAM scoped to the exact routed model ARNs

**Context**: Everything Bedrock-related up to this point was proven from
the local CLI against a broad-access dev IAM profile -- genuinely a real,
non-mocked call, but not yet the actual deployed cloud runtime calling
Bedrock, and not yet under scoped-down IAM. This was the one Phase 4 item
left open, and the user explicitly asked to close it before moving to any
stress-testing work, correctly pointing out that "is the runtime fully
running in the cloud" wasn't true yet while this gap remained.

**Decision**: Added `infra/stacks/bedrock_grant.py`, a small shared
helper (`grant_bedrock_invoke(fn)`) used by both `OrchestrationStack`
(`AgentTaskHandler`, the Step Functions `InvokeAgent` task) and
`ApiStack` (`AskHandler`, the synchronous `/ask` path) -- the two, and
only two, Lambdas that call `HealthAgent.ask()`. Each gets
`CARE_AGENT_NARRATOR_BACKEND=bedrock` in its environment and an IAM
policy statement scoped to `bedrock:InvokeModel` on exactly 4 resource
ARNs.

**Why 4 ARNs, not 1**: A naive scoping to just the inference-profile ARN
(`arn:aws:bedrock:us-east-1:<account>:inference-profile/us.anthropic....`)
looks sufficient but isn't -- cross-region inference profiles route the
actual request to one of several underlying on-demand foundation models
in different regions, and IAM evaluates permission against *both* the
profile resource and whichever underlying foundation-model resource the
request lands on. Didn't assume this from memory: ran
`aws bedrock get-inference-profile --inference-profile-identifier
us.anthropic.claude-haiku-4-5-20251001-v1:0` against the real account
first, which returned the profile's actual 3 routed foundation-model
ARNs (`us-east-1`, `us-east-2`, `us-west-2`). All 4 ARNs (1 profile + 3
models) are hardcoded explicitly in `bedrock_grant.py` -- no wildcard
anywhere in the resource list, verified by
`test_no_iam_policy_uses_wildcard_resource` (an existing regression guard
in both `test_stacks.py` and `test_orchestration_stack.py`) continuing to
pass unchanged.

**Verification against the live account** (all three bypass API
Gateway/Cognito entirely, the same direct-invoke approach used for
Phase 3's live verification -- no browser login needed):
1. Direct `aws lambda invoke` on the deployed `AgentTaskHandler` --
   real Claude Haiku prose returned, `narrator_backend: "bedrock"`,
   `safe: true`.
2. Direct `aws lambda invoke` on the deployed `AskHandler` (API-Gateway
   proxy-event shape) with a supplement/dosing-adjacent question -- real
   Bedrock output, correctly declined to give a specific dose, and the
   resulting DynamoDB item (fetched back by `run_id`, not just trusted
   from the response) shows `narrator_backend: "bedrock"` written by the
   Lambda itself.
3. A real `aws stepfunctions start-execution` against the actual deployed
   state machine, with the same kind of dosing-adjacent adversarial
   question -- `SUCCEEDED` in ~9.5 seconds, comfortably inside the 25-second
   `InvokeAgent` task timeout despite Bedrock's added latency over the
   mock narrator (this was a real open question -- Bedrock's latency
   eating into a timeout budget sized for the near-instant mock path was
   exactly the kind of thing worth actually measuring, not assuming).
4. Cross-checked all three against `CloudWatch`'s `AWS/Bedrock`
   `Invocations` metric for the model: 3 → 6 across exactly these 3 calls,
   re-queried before and after. **Correction (2026-09-05)**: a second
   independent review correctly noted this metric is model-level, not
   caller-level -- it confirms 3 real Bedrock calls happened, not
   specifically that they came from the Lambdas rather than a local
   process using the same account. The actual evidence of Lambda origin is
   that each call was made by invoking the Lambda/state machine directly
   by name, not the CloudWatch count alone.

**Consequence**: This closes Phase 4 completely -- both "at least one
real, non-mocked call" and "scoped IAM in a deployed Lambda" are done and
independently verified, not just code-complete. It also surfaces the
concrete next question the user raised right after this: Bedrock's real
latency (several seconds, not the mock's near-zero) is now a live variable
in the deployed system, worth stress-testing deliberately (concurrent
load, timeout boundaries, throttling behavior) rather than assumed safe
because it worked in ones-of-calls testing. See `docs/AWS_ROADMAP.md` for
what that stress-testing pass should cover.

---

## 2026-09-03 — Live cancel-race test: cancellation lost, and that's informative, not a bug

**Context**: `record_result.py`/`cancel_run.py`'s conditional-write race
was already proven correct via `test_orchestration_lambdas.py` (both
orderings seeded directly, no timing dependency). Deployed live, a real
test fired `cancel_run` immediately after `start_run` for the same
run_id, to see which side actually wins under real network/Lambda timing
rather than a unit test's artificial ordering.

**Observation**: The state machine's own success path won every time
tried. `agent_task`'s work (the mock-narrator path) completes in well
under a second; a cold-started `cancel_run` Lambda's own invoke + a
DynamoDB conditional write round-trip is comparably slow or slower. By the
time `cancel_run` reaches its own conditional write, `record_result` has
usually already claimed `SUCCEEDED`.

**Decision**: Not a bug to fix -- this is the correct, honest behavior of
optimistic concurrency: whoever's write actually lands first wins, and a
task that finishes in ~1 second was never a realistic cancellation target
in the first place. No code change from this entry; it's here so a future
reader (or a comparison against the Azure side's cancellation behavior)
isn't surprised by "cancel didn't seem to do anything" against this
specific fast, synthetic workload.

**Consequence**: This pattern's practical value shows up once a task is
genuinely slow (a real model call taking several seconds to tens of
seconds, external API calls, anything with real latency) -- which is
exactly the situation Phase 4's Bedrock integration will introduce.
Re-testing the cancel race after Phase 4 lands, with a task that actually
takes long enough to plausibly cancel mid-flight, would be a more
meaningful test of this mechanism than repeating today's version.

---

## 2026-09-03 — State machine finalization uses Lambda Tasks, not direct DynamoDB ASL integrations

**Context**: Step Functions can write to DynamoDB two ways: a direct
service integration (`tasks.DynamoUpdateItem`, no Lambda involved) or a
Lambda Task that itself calls `boto3`. The direct-integration route is
"more native" and is what a purist reading of "use Step Functions'
reliability features" might reach for first.

**Decision**: Used Lambda Tasks (`mark_running.py`, `record_result.py`)
for every DynamoDB write instead. Direct ASL integrations require typed
`DynamoAttributeValue` values, and dynamically inserting a *boolean*
(`safe`) sourced from `$.agent_result.safe` into that typed system has no
clean built-in path (`JsonPath.string_at` is for strings; there's no
dynamic-boolean equivalent) — the workaround options were all uglier than
just writing five lines of `boto3` in a Lambda.

**Consequence**: The retry/timeout/catch/choice *orchestration* is still
100% native Step Functions (that's the actual Phase 3 requirement); only
the leaf-level "how does a value get into DynamoDB" step is a thin Lambda
instead of raw ASL. This also made the terminal-state race directly unit
-testable with ordinary `moto` + `boto3` mocking
(`test_orchestration_lambdas.py`), which a pure ASL integration would have
made harder to exercise outside a real deployed state machine.

---

## 2026-09-03 — Execution name = run_id, for a free idempotency property

**Context**: `start_run.py` needed some way to avoid double-starting a run
if a client retries a `POST /runs` call (e.g. after a client-side timeout
that wasn't actually a server failure).

**Decision**: Use `run_id` as the Step Functions execution *name*, not
just data passed in the execution input. For a STANDARD state machine,
starting an execution with a name that's already in use (within Step
Functions' ~90-day execution-history retention) raises
`ExecutionAlreadyExists` rather than starting a second, independent run.
`start_run.py` catches that specific error and treats it as success —
the run is already in flight (or finished); there's nothing new to start.

**Consequence**: This is a *second*, independent idempotency mechanism,
layered on top of (not a replacement for) the DynamoDB conditional-write
terminal-state protection `record_result.py`/`cancel_run.py` implement.
The execution-name check prevents a duplicate *state machine run* from
starting at all; the conditional write protects against races *within* a
single run's lifecycle (e.g. cancel vs. natural completion). Worth noting:
`moto`'s Step Functions mock doesn't actually enforce
`ExecutionAlreadyExists` for duplicate names (verified while writing
tests), so that specific behavior is tested against a directly mocked
boto3 client rather than moto's state-machine simulation.

---

## 2026-09-03 — Cognito's default email never delivered the sign-up code; confirmed via admin API instead

**Context**: `AuthStack`'s User Pool doesn't configure a custom email
sender (no SES integration), so it falls back to `COGNITO_DEFAULT` --
Cognito's own built-in email sending. Three real sign-up attempts through
the Hosted UI (against real Gmail addresses) all stayed stuck in
`UNCONFIRMED`; no verification code email ever arrived (checked spam too).
This is a known limitation of `COGNITO_DEFAULT`: a low daily send quota
and a sender address/reputation that many providers filter as spam --
not something specific to this account or this code.

**Decision**: Rather than set up SES (real domain verification, sending
limits, production-access request -- meaningful scope for what's still a
Phase 2 auth skeleton), confirmed the stuck user directly with
`aws cognito-idp admin-confirm-sign-up`, using admin credentials against a
User Pool this project itself owns and created purely for testing. The
user then signed in normally (password they'd already set) through the
Hosted UI and completed the real PKCE flow end to end.

**Consequence**: Documented as a manual step, not automated -- this is
exactly the kind of thing `AWS_SETUP.md`/`get_dev_token.py`'s "a human has
to click through a real login" boundary already anticipated, just for a
different reason (missing email, not missing browser). If this project
ever needs self-serve sign-up to actually work unattended, SES + a
verified sending domain becomes real, non-optional scope -- worth flagging
explicitly rather than let "add SES" quietly become an assumed given.

---

## 2026-09-03 — Auth enforced at API Gateway, not in the Lambda handler

**Context**: Phase 2 needed to protect `/ask`. One option was to check the
`Authorization` header inside `adapter.py` itself (decode/validate the JWT
in Lambda code).

**Decision**: Use API Gateway's native Cognito JWT authorizer
(`HttpJwtAuthorizer`) on the route instead. A request without a valid token
never invokes the Lambda at all.

**Consequence**: `lambda_src/adapter.py` needed zero changes for Phase 2 —
it still has no idea auth exists. This keeps the handler's own tests
(`test_adapter.py`) entirely about business logic, and keeps auth
concerns testable independently via CloudFormation assertions
(`test_stacks.py`) rather than needing to mock JWT validation inside a
Lambda unit test. It also means a misconfigured/compromised Lambda can't
accidentally skip an auth check that lives in its own code path -- the
check isn't in that code path at all.

---

## 2026-09-03 — App Client is a public client (no secret), ID token not access token

**Context**: Cognito app clients can be "confidential" (have a secret,
suited to a server that can keep it private) or "public" (no secret,
suited to a CLI/native/browser client that can't). `get_dev_token.py` is a
local script with nowhere secure to keep a secret.

**Decision**: `generate_secret=False`, Authorization Code + PKCE flow
(PKCE is specifically the mechanism that makes the public-client,
no-secret case safe against authorization-code interception). The API
Gateway authorizer validates the **ID token**, not the access token --
the ID token's `aud` claim matches the app client ID directly, which is
what `HttpJwtAuthorizer`'s `jwt_audience` check expects for a
Cognito-issued token; the access token carries a `client_id` claim
instead and isn't the conventional shape for this check.

**Consequence**: `get_dev_token.py` exports `CARE_AGENT_ID_TOKEN`, not an
access token. Worth remembering if this is ever compared against how the
Azure side scopes its equivalent (Entra ID access tokens are the more
conventional choice there) -- a concrete example of "same requirement,
different idiomatic answer per platform," which is exactly the kind of
thing this log exists to capture.

---

## 2026-09-03 — Cognito Hosted UI domain prefix is a hardcoded literal

**Context**: Cognito Hosted UI domain prefixes are globally unique across
*all* AWS accounts (they live under `*.auth.<region>.amazoncognito.com`),
not just this one. `cdk synth` also needs to work with no real AWS
credentials at all (CI's fake-account job) -- so the prefix can't be
computed at synth time via a live `sts.get_caller_identity()` call.

**Decision**: `auth_stack.py` defaults `domain_prefix` to the literal
string `"care-agent-470293170577"` (this project's actual account number,
known from having already deployed once), overridable via a constructor
parameter. Tests pass a distinct literal (`"care-agent-test-synth-only"`)
so `cdk synth`-time template generation never depends on any live account
state either.

**Consequence**: Redeploying this stack to a *different* AWS account
requires passing a different `domain_prefix` explicitly (the default would
still technically work — Cognito domain prefixes aren't required to match
the account they're deployed in — but reusing an unrelated account number
as a label would be confusing). Documented here so that's not a surprise.

---

## 2026-09-03 — Phase 1 deployed live; the live URL isn't committed anywhere

**Context**: `cdk deploy --all` succeeded against a real account
(`470293170577`, `us-east-1`); both stacks are live. Phase 1 has no auth by
design (Cognito is Phase 2) -- the `/ask` endpoint is anonymous.

**Decision**: The API URL is not written into any committed file (docs,
tests, scripts). `tests/test_live_endpoint_smoke.py` reads it from
`CARE_AGENT_API_URL`, set manually per-session, not from a checked-in
value. `AWS_ROADMAP.md` documents the one-line `aws cloudformation
describe-stacks` command to fetch it instead.

**Consequence**: Nobody browsing this public repo's history can find and
hit the live anonymous endpoint. The cost exposure from someone finding it
anyway and hammering it is small at this scale (Lambda/DynamoDB/S3 are all
consumption-priced fractions of a cent per request, no Bedrock calls
happen on the default mock-narrator path this endpoint runs), but there's
no reason to make it easier to find than it has to be before Phase 2 adds
real auth.

---

## 2026-09-03 — `cdk bootstrap` failed once: IAM user had no policy attached

**Context**: First `cdk bootstrap` attempt failed:
`AccessDenied: ... dev-cli is not authorized to perform:
cloudformation:DescribeStacks`. The IAM user existed (created per
`AWS_SETUP.md`) but had no policy attached yet -- `AdministratorAccess`
hadn't actually been attached in the console, just planned.

**Decision**: Not a code fix -- confirmed via `aws iam list-attached-user-policies`
that no policy was attached, had the account owner attach
`AdministratorAccess` in the console, re-ran `aws sts get-caller-identity`
to confirm the ARN and account, then retried `cdk bootstrap`, which
succeeded immediately.

**Consequence**: Worth remembering as a first-deploy checklist item on any
new AWS account: `aws iam list-attached-user-policies --user-name <name>`
is a fast way to confirm "did the policy attachment actually take" before
spending time debugging what looks like a CDK/CloudFormation problem but
is actually an IAM console step that didn't happen.

---

## 2026-09-03 — Lambda packaging: plain file copy, no Docker/pip bundling

**Context**: CDK's typical Python Lambda bundling story assumes third-party
dependencies need `pip install --target` inside a Docker container matching
the Lambda runtime. `care_agent`'s default (mock-narrator) path has zero
third-party runtime dependencies — stdlib + `sqlite3` only — and `boto3`
(the only import the Lambda handler itself adds) already ships in the AWS
Lambda Python runtime image.

**Decision**: `infra/build_lambda_asset.py` does a plain `shutil.copytree`
of `care_agent` + `data/` + the handler into one flat staging directory,
which `aws_lambda.Code.from_asset()` zips as-is. No Docker required to
`cdk synth` or `cdk deploy`.

**Consequence**: Simpler, faster synth/deploy, and one fewer moving part to
debug. This breaks the moment any *deployed* Lambda actually needs an
optional narrator backend's SDK (`anthropic`/`openai`/`google-genai`) — at
that point a real bundling step (or a Lambda Layer) becomes necessary. Not
needed yet: Phase 1 only exercises the mock path in the deployed Lambda.

---

## 2026-09-03 — Both stacks use `RemovalPolicy.DESTROY` + S3 auto-delete

**Context**: CDK defaults DynamoDB tables and S3 buckets to `RETAIN` on
stack deletion — the safe default for production data, but it means
`cdk destroy` silently leaves orphaned (billed) resources behind unless you
know to look for them.

**Decision**: Explicit `RemovalPolicy.DESTROY` on both, plus
`auto_delete_objects=True` on the bucket (S3 buckets aren't deletable via
CloudFormation while non-empty otherwise). This is a demo/learning project
holding synthetic data, not a system where accidental data loss on
`cdk destroy` is a real risk.

**Consequence**: `cdk destroy --all` actually tears everything down in one
command — important for the cost-conscious "delete Phase 5's experiment
when done" instruction in `AWS_ROADMAP.md`. Would need revisiting (RETAIN,
point-in-time recovery, deletion protection) if this were ever pointed at
real data.

---

## 2026-09-03 — Found and fixed a real bug via infra unit tests, not just synth

**Context**: While writing `tests/test_adapter.py`, a test asserting the
Lambda handler returns 400 for an empty request body instead sent the
literal JSON value `null` as the body (a plausible real client mistake).
`adapter.py` parsed it successfully (`json.loads("null")` → Python `None`)
and then crashed calling `.get()` on it — an unhandled `AttributeError`
that would have surfaced to a caller as an opaque Lambda platform error,
not a clean 400.

**Decision**: Added an explicit `isinstance(body, dict)` check after
JSON-parsing, before touching any field. Added three regression tests
(`null`, `[]`, and a bare JSON string as the body) alongside the original
malformed-JSON case.

**Consequence**: Direct instance of the process-discipline note in
`AWS_ROADMAP.md`'s checklist — this shipped because a test was written for
an edge case, not because it was manually reasoned about ahead of time.
Worth remembering when evaluating how much confidence to place in code that
*hasn't* had adversarial tests written against it yet (Phases 2–5, still
ahead).

---

## 2026-09-03 — Fresh git history instead of preserving the source repo's

**Context**: `src/care_agent/` started as a copy of an existing private
repo's business logic. That repo's history is fine on its own, but this
repo is public.

**Decision**: Start this repo with a single clean initial commit rather than
`git clone`-ing history forward. No commit message in the source repo
actually names any company, but several early README/docstring *file
contents* did (fixed in this repo's first commit) — publishing that history
would still let anyone browse old diffs and see the original framing.
Starting fresh avoids that entirely, at the cost of losing line-level
`git blame` provenance for code that predates this repo.

**Consequence**: This repo's history begins from a working, tested state,
not from empty. That's a deliberate, disclosed choice, not an attempt to
misrepresent how the code was built.

---

## 2026-09-03 — Package renamed `nuaura_agent` → `care_agent`

**Context**: The source package name and various docstrings referenced a
specific company/hiring context (name, "take-home challenge/assignment"
framing) that doesn't belong in a public, general-purpose repo.

**Decision**: Renamed the package to `care_agent` (short, neutral, reads
naturally as `python -m care_agent ask ...`). Left the synthetic sample
data files' *content* untouched (`sample_bloodwork.json`,
`knowledge_base.jsonl`, etc.) — they're synthetic fixtures with no
copyright or confidentiality concern; some knowledge-base entries still
carry a `source_name` of "Nuaura mock policy" because that's literally
what's in the data, not something worth hand-editing out of a fixture file.
Renamed the narrator-backend env var `NUAURA_NARRATOR_BACKEND` →
`CARE_AGENT_NARRATOR_BACKEND` for the same reason.

**Alternatives considered**: `clinical_agent_core` — rejected only for
being longer with no added clarity.

**Consequence**: Anyone diffing this repo against the original source will
see an import-path-wide rename plus prose edits in ~15 files; the actual
reasoning/safety/retrieval logic is untouched (verified: full test suite,
ruff, and mypy all pass identically before and after the rename).

---

## 2026-09-03 — Kernel starts at its original (simpler) maturity level, not backfilled to match the Azure counterpart

**Context**: The Azure-side counterpart project has gone through
substantially more hardening (multiple ADRs, auth deployment, durable
orchestration reliability semantics, a FinOps audit, many more edge-case
test files) than this kernel has at the point of import.

**Decision**: Import the kernel as-is, at its current maturity, rather than
trying to backfill it to architectural parity with the Azure side before
starting the AWS build-out. The comparison this project is actually after
is the *cloud-native deployment layer* (compute, orchestration, state,
identity, managed-model integration) — not byte-for-byte identical business
logic. The kernel will get hardened *in parallel*, phase by phase, the same
way the Azure side was, and that hardening process is itself one of the
things worth comparing.

**Consequence**: Early roadmap phases here will look "behind" the Azure
side's current state at a glance. That's expected and disclosed, not a
gap to hide.

---

## 2026-09-19 — Safety checks now carry a severity; `AgentTrace` records why a fallback happened

**Context**: An architecture discussion comparing this project's narrator/
safety split against two sibling reference implementations (Azure
`ClinicalReasoner`, and a separate Construction Intelligence agent)
concluded that expanding this kernel's reasoning capability (a future
tool-orchestration planner, replacing the current 5-intent classifier)
would have its payoff capped by how coarse `run_safety_checks`'
pass/fail judgment is today: any failed check, regardless of kind,
triggers the same full fallback to the deterministic mock narrator, with
no way to tell — short of reading every `rejected_draft` by hand — a
narrator that tried to diagnose someone apart from one that tripped
`numeric_grounding` on an edge case this project has already found and
fixed several of (hyphenated compounds, unrecognized units, list
markers; see `safety.py`'s own module docstring).

**Decision**: Give every `SafetyCheck` a `severity`: `"hard"` for
`non_empty`/`no_diagnosis`/`no_dosing` (unambiguous policy violations,
never the check's own fault), `"soft"` for `numeric_grounding` (the one
check with a real, documented false-positive history). `SafetyReport`
gains `has_hard_failure`. `AgentTrace` gains `disposition`
(`"answered"` / `"answered_after_hard_fallback"` /
`"answered_after_soft_fallback"`), set once in `agent.py` right after
the existing fallback decision. The Workbench's `TraceView` now shows
this as a colored pill plus a per-check severity tag.

**This is deliberately an observability-only change**: the fallback
behavior itself — any failed check, hard or soft, replaces the draft
with the mock narrator's output — is byte-for-byte unchanged. Nothing
about what reaches the patient got looser. What changed is that an
operator reviewing traces across many requests can now tell, without
reading prose, whether a fallback was a genuine safety catch or a
grounding-check false positive worth investigating — the same
"claim verification chain" pattern this project's own market research
found converging on in bank model-risk guidance (SR 26-2) and FDA CDS
review.

**Alternatives considered**: Loosening the fallback trigger itself for
soft-only failures (e.g. serving the LLM draft with a "needs review"
flag instead of the mock replacement) — rejected for now. That would be
a real weakening of the patient-facing guarantee with no queue or
reviewer to route it to yet; the value here is purely in making the
*existing* fallback's cause legible, not in changing what fires it.

**Consequence**: `SafetyCheck.severity` and `AgentTrace.disposition` are
new required-with-default fields — existing callers/tests are
unaffected (verified: full suite + ruff + frontend tsc/vitest/eslint all
pass unchanged). A future tool-orchestration layer's partial/uncertain
results have a real field to report against later, instead of nothing.

---

## 2026-09-19 — Source-data plausibility bounds, independent of the dataset's own classification

**Context**: Item 2 of the "探亲窗口" engineering list from the same
architecture discussion as the previous entry. Every existing safety
check verifies that a number in the narrated answer traces back to a
`GroundedFact` — but a `GroundedFact` built from a data-entry error (a
decimal point dropped, a unit confused) is still, mechanically,
"grounded." Nothing in this project previously questioned whether a raw
source value was physiologically possible at all, only whether the
narrator's prose matched it.

**Decision**: New module `plausibility.py` — a per-`concept_id` table of
outer physiological bounds (wide: severe-but-real documented cases must
pass; only true implausibility — a sign error, a value no living patient
has ever had — should fail), and `assess_plausibility()`. Wired into
`agent.py` via `reasoning.implausible_value_limitations()`, which runs
over *every* biomarker in the latest panel (not just `rank_focus_markers`'
output, which only considers markers the dataset already classifies as
abnormal) and appends a `Limitation` for anything outside bounds.

**Caught before shipping, not found live**: the first draft stated the
numeric bounds themselves in the limitation text (e.g. "outside 0-1000
mg/dL"). Every narrator template renders `Limitation.detail` into the
final answer text, which `verify_numeric_grounding` then scans for
*every* number — so those bounds, being this project's own constants
rather than a patient fact, would have failed the very safety check this
whole exercise is about, on the deterministic mock narrator's own output,
every time this new check fired. Fixed by dropping the bounds from the
rendered text and instead grounding only the flagged value itself (a new
`GroundedFact` with `source_type="bloodwork"`).

**Deliberately does not exclude a flagged marker from
`rank_focus_markers`/`detect_metabolic_priority_pattern`** — this is a
disclosure added on top of the existing pipeline, not a change to
ranking or pattern-detection logic, to keep this change narrowly scoped.
Excluding a flagged value from those (so an implausible number can't
itself drive a "see a clinician" recommendation) is a reasonable next
step, not done here.

**Not a `kb_*` policy rule**: unlike this project's other numbered
checks, these bounds don't trace to a `Nuaura mock policy` entry in
`knowledge_base.jsonl` — see `plausibility.py`'s own docstring for the
explicit "not clinically validated, a real deployment needs a clinician
to own this table" caveat, consistent with this project's standing
non-clinical/synthetic-reference-implementation framing.

**Consequence**: New `Limitation(kind="implausible_value", ...)` entries
can now appear in `brief.limitations`/`trace.limitations`; existing
callers are unaffected (verified: full suite + ruff + mypy on the
touched files all pass, no existing test's fixture data crosses any of
the new bounds).

---

## 2026-09-19 — Narrator/model-provider changes are already gated by the eval suite; this makes that a stated policy, not just an accident of CI order

**Context**: Item 3 of the same "探亲窗口" list. Enterprise model-risk
guidance this project's market research turned up (SR 26-2's extension of
bank model-risk management to generative/agentic AI) flags a specific
concern: "the core model is usually a third-party system that can change
without notice." This project already has a real answer to that —
swappable narrator backends behind one interface (`_select_narrator()`),
and a capability regression suite (`care_agent.eval`) — but nothing
stated that the second thing is *supposed* to gate the first.

**Decision**: State it as policy, not just document a coincidence: any
change to a narrator backend, its prompt, or its underlying model version
must pass `python -m care_agent eval-capabilities` before merge. No new
CI wiring was needed — `.github/workflows/ci.yml`'s `test` job already
runs `eval-capabilities` unconditionally on every push to `main` and
every PR (line ~82), so this has been mechanically true since that job
was added; this entry is the missing sentence that says so on purpose.

**Consequence**: A reviewer asking "what stops a narrator/model swap from
silently regressing behavior" now has a one-line, already-enforced
answer, not just an inference from reading the CI config.

---

## 2026-09-20 — V2: a bounded tool-calling compound-reasoning engine, additive to the fixed 5-intent classifier

**Context**: Item 4 of the "探亲窗口" list, and the largest one. The fixed
5-intent classifier (`intent.py`) can only trigger one intent per
question, so it structurally cannot answer a compound, multi-hop
question ("compare my LDL and A1C trends against my reported diet
change") — not a bug, a deliberate original scope boundary, but a real
coverage ceiling. An architecture discussion the same day (see this
project's own AWS-vs-Azure-vs-Construction-Intelligence comparison
history) established the right shape for closing it without weakening
anything: give an LLM real autonomy over *which* deterministic tools to
call and in what combination, never over what a tool computes or
whether its output is safe — the same "epistemic authority" boundary
this project holds everywhere else.

**Why hand-written, not Bedrock Agents / AgentCore / Bedrock Managed
Agents**: live-researched the same day (three genuinely different AWS
offerings as of 2026-09, not one). Bedrock Agents and Bedrock Managed
Agents both hand the tool-calling loop itself to a managed service —
this project's independent, hard-gated `safety.run_safety_checks`
(never bypassable, never delegated) is easiest to keep airtight when
this project's own code owns the whole loop, not a managed runtime.
AgentCore is a different kind of thing entirely — secure session
isolation, long-running (up to 8h) execution, MCP-based dynamic tool
discovery, managed memory — infrastructure for *running* arbitrary agent
code securely at scale, not an orchestration framework that replaces
hand-written planning logic. None of AgentCore's actual capabilities are
needed here: this project's tools are pure, side-effect-free reads
against synthetic data (nothing to sandbox), a turn is bounded to a
handful of tool calls (no long-running-execution need), and dynamic
runtime tool discovery is specifically the kind of *expanded* autonomy
this project's whole design deliberately withholds from the model (a
dynamically-discovered tool has no guarantee it respects this project's
grounding contract). Session's own precedent, independently verified,
not just asserted: the Construction Intelligence sibling project's SPEC-M16
V2 made the identical choice on Azure — a hand-written tool-calling loop
against Azure OpenAI's native Chat Completions `tools=` parameter, not a
higher-level "Azure AI Agent Service."

**What was explicitly *not* adopted from that same SPEC-M16 precedent**:
its D-062 decision downgraded a numeric-narrative-consistency check from
a hard block to a disclosed-caveat-and-ship-anyway, based on a measured
finding that the check's real-usage false-positive rate exceeded its
true-positive rate, combined with that project's own "decision support,
user shares responsibility" positioning. This project's `verify_numeric_
grounding` catches a categorically different class of error (an invented
fact, not an imprecisely-phrased real one) for an audience (a healthcare
worker under real time pressure, per this project's own positioning
discussion) whose realistic ability to independently re-verify every
number is exactly what FDA's 2026 CDS guidance says can't be assumed for
a tool whose whole value is *reducing* chart-review time. This project's
hard fallback-on-any-failure stays completely unchanged for V2 — see
"one safety gate, not two" below.

**Decision — architecture**:
- `orchestrator.py` (new): `TOOL_SPECS` (seven tools, each a thin wrapper
  over existing deterministic functions this project already had --
  `trend.compute_trend`, `reasoning.rank_focus_markers`, `reasoning.
  build_supplement_cautions`, `QuestionnaireContext.fact`, catalog
  lookups, the existing retriever -- no new computation logic anywhere),
  `capability_gate` (rejects an unknown tool name or an out-of-vocabulary
  `concept_id` before execution), `run_compound_reasoning` (plan ->
  gate -> execute -> build a `Brief`, bounded to `MAX_ITERATIONS=2`: one
  initial plan plus one bounded repair attempt if every call in it was
  rejected -- never an unbounded loop, and an honest `unsupported_
  request` `Limitation` if the repair also fails, never a guess).
- `tool_planner.py` (new): `BedrockToolPlanner`, using the Converse API's
  `toolConfig` (tool use) against the same Claude Haiku 4.5 model
  `narrator/bedrock_narrator.py` already defaults to for narration --
  live-confirmed to support tool use (2026-09 AWS docs). Off by default;
  `ask_compound()` requires an explicit `planner` argument rather than
  defaulting the way `narrator`/`retriever` do, because there is no safe
  deterministic default that can plan an open-ended tool combination the
  way `MockNarrator` can safely template a fixed intent.
- `agent.py`: `ask()`'s narrate-verify-fallback tail extracted unchanged
  into `_narrate_and_verify()` (a pure, behavior-preserving refactor,
  verified by the full existing suite passing identically before and
  after) so the new `ask_compound()` reuses the *exact same* method, not
  a reimplementation. `ask_compound()` runs the same deterministic
  `classify()` red-flag check *before* any tool-calling planning --
  identical, unconditional gate to `ask()`'s, proven by a test asserting
  the planner is never even invoked for an emergency-phrased question.

**One safety gate, not two**: `run_compound_reasoning` returns the same
`Brief` type `ask()`'s fixed pipeline produces, which is why
`_narrate_and_verify` needs no V2-specific branch at all -- a V2-built
Brief goes through identical hard-fallback-on-any-failure behavior,
verified directly by a test reusing this project's existing
`_UnsafeFakeNarrator` pattern against the compound-reasoning path.

**Two real bugs found live, not by inspection or code review** (both
regression-tested):
1. `safety._sentence_context` treated *any* `.` as a sentence boundary,
   including a decimal point. A trend fact rendered as one sentence
   naming two decimal values for the same marker ("HbA1c trend: 5.8 % on
   ... -> 6.1 % on ...") had its *second* value's marker-name-proximity
   window truncated by the *first* value's own decimal point, excluding
   "HbA1c" (named earlier in the same sentence) and producing a false
   "no matching marker name nearby" grounding failure on a genuinely
   grounded value. No V1 code path had ever produced two decimals for
   one marker in one sentence before this. Fixed: a "." only counts as a
   boundary when it isn't flanked by digits on both sides.
2. `mock_narrator._compose_general`'s `mentioned_markers`/`focus_items`/
   `grounded_facts` branches are mutually exclusive (`if`/`elif`/`elif`)
   -- a compound question gathering facts from multiple tool categories
   (e.g. allergies + a marker snapshot) rendered only whichever category
   populated the first-checked field, silently dropping the rest from
   the narrated answer even though they were correctly grounded and
   present in the trace. Fixed with a dedicated `_compose_compound`
   template (dispatched on `brief.intent == COMPOUND_REASONING`, a new
   intent constant added to `intent.py`, not `orchestrator.py`, to keep
   this project's one-way dependency direction intact -- `narrator`
   never imports `orchestrator`) that always renders every gathered fact,
   never picks one shape.

**Consequence**: Three real compound-question classes verified live end
to end (multi-marker-trend + questionnaire cross-reference; cross-marker
priority beyond the one hardcoded metabolic pattern; supplement safety +
allergy + marker snapshot) plus the honest-refusal path for an
out-of-vocabulary question. `ask()` is provably unchanged (full suite
passes identically). Deferred, not built here (see this session's own
scoping discussion): multi-turn conversational memory, streaming, and
the `patient`/`clinician` narrator persona toggle -- the last of these
is the planned immediate next step, not abandoned.

---

## 2026-09-20 — Persona (`patient`/`clinician`) changes narration framing only, never facts or the safety gate

**Context**: The planned immediate follow-up to V2 (previous entry). Two
personas, one engine: the earlier architecture discussion concluded a
clinician audience changes *what disclaimer framing is appropriate*
("please see a doctor" vs. "this supports, doesn't replace, your
judgment"), never what counts as a grounded fact or what triggers a
safety fallback -- that boundary is identical for both personas and
this change doesn't touch it anywhere.

**Decision**: `Brief.persona: str = "patient"` (new field, defaults to
today's only behavior). `narrator/_prompt.py` gains `PATIENT_SYSTEM_
PROMPT`/`CLINICIAN_SYSTEM_PROMPT` (sharing `_SHARED_RULES` -- the actual
grounding/no-diagnosis/no-dosing rules, identical for both) and
`system_prompt_for(persona)`; all five LLM narrators (Bedrock/Anthropic/
OpenAI/Google/Ollama) now call it instead of importing a bare constant --
`SYSTEM_PROMPT` kept as a backward-compatible alias, not removed.
`mock_narrator.py` gains `_not_a_diagnosis_line`/`_general_disclaimer_
line`, used at the three spots that previously hardcoded patient-only
disclaimer text (`_compose_priority_focus`, `_compose_general`,
`_compose_compound`). `ask()` and `ask_compound()` both gain a
`persona: str = "patient"` parameter, set once on the `Brief` right after
it's constructed.

**Deliberately narrow scope, stated up front, not discovered as a
limitation later**: this pass changes disclaimer text and LLM system-prompt
framing only -- it does not rewrite every template's pronouns ("Your
latest X" stays as-is for both personas) or loosen `check_no_diagnosis`'s
patterns toward differential-style clinical language. That second change
was explicitly discussed and deferred (see previous architecture
discussion): it's a real, separate design question about how much
diagnostic-adjacent latitude a clinician audience should get, not
something to fold into a disclaimer-wording pass.

**Consequence**: `ask()` and `ask_compound()` with no `persona` argument
are byte-identical to before this feature existed (regression-tested).
`AgentTrace`/API response shapes are unchanged -- persona is a caller-
supplied input, not a new output field.

---

## 2026-09-20 — Browser demo for V2: a local-only dev server + SSE, not a real AWS deployment

**Context**: Wanted to see V2 (compound reasoning) working in the real
Workbench UI, specifically with live tool-calling *progress* visible --
not a long silent wait followed by a sudden result. The production
frontend (`frontend/src/api.ts`) only ever talks to the real, deployed,
Cognito-authenticated `CareAgentApiStack`. Wiring V2 into that path for
real would mean a new Lambda handler, an API Gateway route, IAM changes,
and an actual `cdk deploy` -- real AWS cost and deployment risk, and this
project's own established pattern (Stage B's "real spend starts here"
markers) reserves that kind of step for a later, explicit decision, not
something to fold into a demo/verification pass.

**Decision**: `scripts/dev_server.py` -- a local, unauthenticated FastAPI
server (new `devserver` extra: `fastapi`/`uvicorn`, kept out of `dev` so
the core test/lint loop never needs it), never deployed, with no relation
to the real Lambda/API Gateway path. `POST /ask` (V1, synchronous) and
`GET /ask_compound/stream` (V2, Server-Sent Events) both call the exact
same `HealthAgent` methods this project's tests already exercise --
real Bedrock calls (`BedrockToolPlanner` + `BedrockNarrator`), not a
scripted fake, so the demo shows this project's real behavior, including
real latency and real safety-fallback behavior.

**No token streaming, and this is a considered choice, not a shortcut**:
`run_compound_reasoning` and `ask_compound` gained an optional `on_stage`
callback (backward-compatible, defaults to a no-op) fired at each real
checkpoint -- planning, tool calls, narration. `dev_server.py` bridges
this synchronous callback to an async SSE stream with a background
thread + a `queue.Queue`. This project's own process is short and
coarse-grained (one planner round-trip in the common case, then fast
in-process tool calls, then one narration round-trip -- confirmed by a
live timing run: ~1.5s to the planning result, ~2.9s more to the final
answer) -- a small, fixed number of stage updates is the right amount of
infrastructure for that shape, not per-token streaming (which SPEC-M16's
own V2 needed and explicitly called "genuinely new infrastructure" --
this project's shape doesn't require it).

**Frontend**: `CompoundDemo.tsx` (new, standalone -- not folded into the
existing, already-tested `AskForm.tsx` and its polling/generation-counter
logic) uses the browser's native `EventSource` against
`http://localhost:8000` (the dev server), independent of
`config.apiBaseUrl`/Cognito. Renders each `stage` event as it arrives (a
live-updating list, most recent highlighted) and the final `done` event
through the existing `TraceView`/`Markdown` components unchanged --
proving those components need no V2-specific handling, the same "one
trace shape, not two" property `ask_compound` was built around.

**Verified live, in a real browser, not just unit-tested**: three stage
updates rendered progressively before the final answer (screenshotted at
each step); the final render correctly showed the disposition badge,
per-check severity tags, grounded facts, and an honest `missing_data`
limitation (a guessed questionnaire field name that wasn't real) -- the
same `TraceView` built for V1's item-1 work, unmodified, rendering a
V2 result correctly. (Browser testing required temporarily bypassing
`App.tsx`'s Cognito gate -- no interactive login credentials were
available in this environment; the bypass was reverted immediately
after verification, confirmed via `git diff` showing only the real,
permanent `CompoundDemo` mount remaining.)

**Consequence**: `care_agent[devserver]` is a new, clearly-scoped local
tool, not a production surface -- deploying V2 for real (new Lambda,
API Gateway route, real `cdk deploy`) remains a separate, later,
explicitly-costed decision, exactly as this project's standing pattern
requires.

---

## 2026-09-20 — Two more found live testing the browser demo: a real trace gap, and a UX restructure

**Context**: The owner tested the browser demo directly and asked whether
V2's answers were ever grounded in retrieved knowledge-base chunks --
the evidence panel showed none. Investigated live rather than assumed.

**Bug found**: `search_knowledge` was working correctly (confirmed via
the tool call's own `result_summary`, and via calling the retriever
directly with the same query -- both returned real, relevant chunks),
but `ask_compound()` never copied `brief.retrieved_chunks` onto `trace`
the way `ask()` does (`trace.retrieved_chunks = retrieved`). A real,
successful retrieval never reached the Workbench's evidence panel at
all. Fixed (`agent.py`, both the red-flag branch and the main path);
regression-tested, and confirmed the test actually catches the bug (it
fails against the pre-fix code, not just passes trivially).

**Separately confirmed, not a bug**: the planner only calls
`search_knowledge` when a question's own phrasing calls for general
background (verified live: a lifestyle-advice question triggered it, a
pure trend-comparison question correctly did not). When it isn't
called, the narrator's *explanatory* prose (e.g. what LDL-C means)
still draws on the model's own general medical knowledge -- this is not
new to V2; V1's LLM narrators have always worked this way. Only
*numeric* claims are hard-gated against `grounded_facts` for either
engine; unstructured explanatory framing was never gated by the
knowledge base, retrieved or not.

**UX restructure**: `AskForm`/`CompoundDemo` were shown stacked
(V1 always on top, V2 always below) -- the owner asked for a single
top-level switch instead, mirroring SPEC-M16's own "Engine: V1/V2"
toggle pattern. `App.tsx` gained an `engine: "v1" | "v2"` selector that
swaps the entire view rather than showing both; verified live in the
browser (same temporary-auth-bypass-then-revert method as the previous
entry, confirmed via `git diff` afterward).

**Deferred, explicitly, not started**: giving V2 the same Step
Functions / Queue async execution modes V1 has. This is a materially
larger, different task than everything in this entry or the previous
one -- both of those stayed entirely local (dev server, browser-only
verification). Step Functions/Queue execution is real, deployed AWS
infrastructure (a new Lambda handler running `ask_compound`, CDK
changes to `infra/stacks/orchestration_stack.py`/`queue_stack.py` or
new equivalents, IAM for that Lambda to call Bedrock's tool-use
Converse API, an actual `cdk deploy`) -- exactly the kind of real-cost,
real-deployment step this project's standing pattern (Stage B, the
previous dev-server entry) reserves for its own separate, explicit
decision, not something to fold into today's local-only work.

---

## 2026-09-20 — V2's real production deployment path: extend `/ask`, not a new route

**Context**: The owner asked to build V2's real, deployed path (not just
the local dev server), then deploy and test it live in production --
explicitly Option B of three offered. Every deployed Lambda entrypoint
(`adapter.py`, `agent_task.py`, `process_job.py`) was confirmed (by
grep, not assumption) to call only `HealthAgent.ask()` -- `ask_compound`
had no production route at all before this entry.

**Decision**: Extend the existing synchronous `/ask` route and
`adapter.py` handler with an optional `engine: "v1" | "v2"` field
(default `"v1"`, reproducing today's exact behavior when omitted) and an
optional `persona: "patient" | "clinician"` field (default `"patient"`)
-- not a new route, new Lambda, or new CDK integration. This reuses
`adapter.py`'s entire existing run-tracking/evidence-write/error-handling
logic unchanged rather than duplicating it, and mirrors the pattern this
project's own Construction Intelligence sibling used for the identical
problem (`ChatRequest.engine`, SPEC-M16). `agent_runtime.py` gains a
`tool_planner = BedrockToolPlanner()` constructed alongside `agent`,
using the same lazy-boto3-client pattern (no network call or credential
check at construction, only at an actual `.propose_plan()` call) --
`infra/tests/test_adapter.py`'s new `engine="v2"` tests monkeypatch this
with a scripted fake, never making a real Bedrock call in CI, the same
convention `tests/test_orchestrator.py` already established for the
`care_agent` package's own tests. Deliberately scoped to the synchronous
`/ask` path only -- `/runs` (Step Functions) and `/jobs` (SQS) giving
`engine="v2"` support is explicitly still deferred (see the previous
"browser demo" entry's own deferred-scope note); this entry doesn't
change that.

**No new IAM grant needed**: `BedrockToolPlanner` calls the same
Converse API (`self._client.converse(...)`) as `BedrockNarrator`, which
`grant_bedrock_invoke` already scopes `bedrock:InvokeModel` for on the
exact model ARN both use -- confirmed by `BedrockNarrator`'s own Converse
calls already working in production under this exact grant, not assumed
from documentation. Tool-use (`toolConfig`) is an additional request
parameter on the same API call, not a different IAM action.

**Verification before deploy, not after**: `cdk synth --quiet` succeeded
for every stack (confirms the Lambda asset bundling -- a plain file copy
of `src/care_agent/`, see `build_lambda_asset.py` -- picks up the new
`orchestrator.py`/`plausibility.py`/`tool_planner.py` modules with no
build-step changes, and that `boto3` alone, already in the Lambda
runtime image, covers `BedrockToolPlanner`'s only dependency, same as
`BedrockNarrator`'s). The full infra suite (165 tests, cdk-nag included)
and the full `care_agent` suite (248 tests) both pass. A new live smoke
test (`test_live_engine_v2_compound_reasoning_returns_a_real_safe_
grounded_answer`, `infra/tests/test_live_endpoint_smoke.py`) is added
but not yet run -- it requires a real deployed endpoint, and is the
actual production verification step for this work, to be run once
`cdk deploy` completes.

**Frontend -- added after the owner asked for a real, working browser
test, not just an API-level one**: `CompoundDemo.tsx` (the "V2" tab)
still targets only the local dev server and says so in its own UI text
-- left as-is, since making it target the real deployed endpoint would
mean losing its live SSE progress display for no gain the "V1" tab
doesn't already give more simply (see below). Instead, `AskForm.tsx`'s
existing "Ask" (sync) mode -- which already correctly authenticates
against the real deployed API via `authedFetch`/Cognito, unlike
`CompoundDemo` -- gained `Engine` (`v1`/`v2`) and `Persona` selectors.
This is the real, production-authenticated way to exercise `engine="v2"`
from the actual Workbench UI: no new component, no new auth path, just
two `<select>`s and `askQuestion()` gaining two optional parameters
(`api.ts`). Verified locally (`tsc`/`eslint`/`vitest` clean, and the
dropdown itself screenshotted live in a browser) before commit.

**Consequence**: `cdk deploy` is the one remaining step to make any of
this real in production -- explicitly not run as part of this entry,
per this project's standing "real deployment is its own deliberate
decision" pattern (Stage B, the dev-server entry).

---

## 2026-09-20 — Real mypy errors found by CI, masked locally by an unrelated venv difference

**Context**: CI's `test` job failed `mypy src` on all three Python
versions after the first push of this session's V2 work. Every local
`mypy` invocation *this entire session* had instead hit a pre-existing,
unrelated failure -- `numpy/__init__.pyi:737: error: Type statement is
only supported in Python 3.12 and greater` -- and each time, that was
treated as "pre-existing, unrelated to my changes" and mypy verification
was skipped rather than actually completed. That reasoning was correct
about the *cause* (this venv's numpy, pulled in by earlier
Stage-A/vector-retrieval work, really is incompatible with this venv's
Python 3.14 when mypy targets `python_version = "3.10"`) but wrong about
the *conclusion*: "mypy never got far enough to check my new code" is not
the same as "my new code is mypy-clean," and every check this session
silently treated the former as proof of the latter.

**What CI's clean venv exposed, once mypy actually ran to completion**:
`pip install -e ".[dev]"` (CI's exact install, and this project's own
"core tests need no LLM SDK" design) never installs numpy at all, so
CI's mypy hit no stub conflict and found 5 real type errors in this
session's new code:

- Two `Panel | None`/`float | None` narrowing gaps in `orchestrator.py`
  (accessing `.measurement_date`/`.panel_id` after checking `marker`,
  not `latest_panel`, for `None`; calling `float()` on `TrendResult`
  fields mypy can't infer are set from `available=True` alone) -- fixed
  with an explicit `latest_panel is None` check and an `assert` matching
  the exact pattern `mock_narrator.py`'s own `_compose_trend` already
  uses for the identical guarantee.
- A variable-name collision in `agent.py`: a new loop introduced earlier
  in `ask()` reused the name `marker` for a non-Optional `Biomarker`,
  and mypy's whole-function flow analysis then rejected a *later*,
  pre-existing reassignment of the same name to a `Biomarker | None`.
  Renamed the new loop's variable to `panel_marker`.
- A local `disposition: str` variable assigned into `AgentTrace.
  disposition`'s narrower `Literal[...]` field -- `str` isn't assignable
  to a `Literal` even though this code only ever sets one of the three
  literal values at runtime. Fixed with a shared `_Disposition` type
  alias matching `models.py`'s field type exactly, imported once, not
  duplicated.

**Fixed the verification gap itself, not just the errors**: built a
throwaway venv (`python3 -m venv`, then `pip install -e ".[dev]"` only,
no `bedrock`/`llm`/`chroma` extras) to reproduce CI's exact dependency
set locally before pushing again -- confirmed it has no numpy, and that
`mypy src`, `pytest`, `ruff check`, and `ruff format --check` all pass
identically to what CI actually runs, not what this session's own
polluted venv happened to allow through.

**Consequence**: the standing lesson, not just this one fix -- a check
that fails to *run* is not evidence of a check that *passed*, and this
project's own venv accumulating optional extras across many phases
(chroma, bedrock, llm) had quietly made local mypy verification
meaningless for an unknown number of prior changes, not just this one.
A clean, dev-extra-only venv is now the standard for verifying anything
before pushing, not this session's main working venv.

---

## 2026-09-21 — Gate the local-only V2 streaming demo behind an actual localhost check; redesign the Workbench UI

**Context**: The user tested the real deployed CloudFront Workbench
themselves, clicked the top-level "V2 -- tool-calling agent (local demo)"
tab, and hit a real, user-facing error: "Could not reach the local dev
server at http://localhost:8000." This was `CompoundDemo.tsx` working
exactly as documented (it deliberately only ever talks to
`scripts/dev_server.py` on `localhost:8000`) -- but presenting it as an
equal top-level tab on the production site meant a real user could click
into a dead end with no way to know it was never meant to work there.
The user also separately asked whether the whole UI should look more
professional, "像工作台" (like a real workbench).

**Decision**: Two changes, made together since both concern the same
surface. (1) `App.tsx` now computes `IS_LOCAL_DEV` from
`window.location.hostname` and only renders the "Local streaming demo
(dev only)" tab when true -- on the real deployed site it simply cannot
appear, rather than appearing and failing. The real, working way to
exercise V2 against the actual production API stays exactly where it
already was: `AskForm.tsx`'s "Ask" mode Engine selector (added in the
previous entry), which is what was actually used for the production
smoke tests. (2) A real visual pass on `index.css` and the page shell:
a dark sticky top bar with a wordmark (replacing a plain `<h1>` at the
top of a narrow column), a deliberate slate/blue palette via CSS custom
properties, card surfaces with borders + soft shadows, a monospace font
stack for IDs/code/run_ids, segmented-control-style tabs, and a mobile
layout fix (the tab row now uses `grid-template-columns:
repeat(auto-fit, minmax(...))` under 480px instead of an uneven
flex-wrap). No component logic changed beyond the `App.tsx` gating --
this was a CSS/layout pass, verified visually in the browser pane at
both desktop and mobile widths, plus `tsc -b`, `eslint`, `vitest`, and
`vite build` all passing.

**Alternatives considered**: Pointing `CompoundDemo.tsx` itself at the
real deployed, authenticated API (losing the local dev server's simple
unauthenticated `EventSource`, and duplicating what `AskForm.tsx`'s
Engine selector already does against the real Lambda, which does not
stream SSE progress). Rejected as unnecessary duplication -- the
project doesn't need two different production-facing ways to run V2,
and the live-progress SSE demo's actual value (showing tool-calling
stages as they happen) is a local-development/demo aid, not something
the deployed synchronous Lambda path currently supports anyway.

**Consequence**: The production Workbench no longer offers a tab that
is guaranteed to fail for every real visitor. Local development keeps
the exact same live-streaming demo capability, unchanged, just no
longer surfaced where it can't work.

---

## 2026-09-21 — Persona was silently dropped on the Step Functions and Queue async paths

**Context**: While testing the redesigned Workbench in production, the
user noticed that `AskForm.tsx`'s Persona selector is shown for all
three modes (Ask/sync, Start run/Step Functions, Enqueue job/Queue) but
only actually changed the answer for "Ask". Tracing it: `startRun` and
`enqueueJob` in `api.ts` never accepted a `persona` argument at all, so
nothing was ever sent; `start_run.py` and `enqueue_job.py` never read
`persona` from the request body; and `agent_task.py`/`process_job.py`
(the Lambdas that actually call `HealthAgent.ask()` for these two paths)
never passed a `persona` kwarg, so `ask()`'s own `persona: str =
"patient"` default silently applied regardless of the UI selection. This
was a real, silent gap, not a deliberate scope boundary -- unlike V2 on
these same two paths, which *is* a deliberate, documented deferral (see
several entries above).

**Decision**: Threaded `persona` all the way through both async paths,
mirroring `adapter.py`'s existing validation exactly (`persona` optional,
defaults to `"patient"`, 400 if supplied and not one of
`"patient"`/`"clinician"`). For Step Functions: `start_run.py` validates
and adds it to the Step Functions execution input; `agent_task.py` reads
`event.get("persona", "patient")` and passes it to `ask()`. For Queue:
`enqueue_job.py` validates and adds it to the SQS message body;
`process_job.py` reads `message.get("persona", "patient")` and passes it
to `ask()`. Frontend: `startRun`/`enqueueJob` in `api.ts` gained an
optional `persona` parameter; `AskForm.tsx`'s submit handler now passes
its `persona` state through for the async modes, same as it already did
for sync. Added regression tests for both paths (invalid-persona-400,
and a real clinician-persona run producing the "decision-support"
wording) mirroring `test_adapter.py`'s existing coverage for the same
behavior on the sync path.

Also, separately (same investigation session): the "Tool calls" section
of `TraceView.tsx` was a collapsed `<details>` by default -- easy to
miss entirely, which the user reported as "the tool-call steps I used to
see are gone" after the previous redesign. It was never removed, just
easy to overlook; changed to render only when `tool_calls` is non-empty,
and to be open by default when it does. True live/streaming progress
during a production V2 run (what `CompoundDemo.tsx`'s local SSE endpoint
shows) is a separate, larger question -- the deployed synchronous `/ask`
Lambda doesn't currently support streaming a response at all (would need
Lambda response streaming via a Function URL, or a WebSocket API, neither
of which exists yet) -- not something this fix attempts.

**Consequence**: The Persona selector now does what it visibly claims to
do on every mode, not just one. Verified with a from-scratch clean venv
matching CI's exact infra job (`ruff check . --line-length=140`, `mypy
stacks app.py lambda_src build_lambda_asset.py scripts/get_dev_token.py
scripts/stress_test.py --ignore-missing-imports`, `pytest tests/ -v`
-- 172 passed, `cdk synth --quiet`), per the standing lesson from the
previous entry that a locally-skipped check is not the same as a passing
one. Note infra's CI job runs `ruff check` only, not `ruff format
--check` (that's specific to `src`/`tests`, per `.github/workflows/
ci.yml`) -- worth remembering before assuming a `ruff format` diff on an
infra file is something CI would actually catch.

---

## 2026-09-21 — The Step Functions persona fix from the previous entry was still incomplete

**Context**: After deploying the previous entry's fix, live production
testing (7 representative real requests across both engines, both
personas, and both async paths, via the real deployed API with a real
Cognito token) found that the Queue path correctly produced
clinician-framed prose, but the **Step Functions path did not** -- its
answer read as patient-framed prose for the same question and the same
`persona: "clinician"` request. `start_run.py` does put `persona` into
the execution's top-level input (verified by the previous entry's own
regression test), so the drop had to be somewhere between that input and
`agent_task.py`.

Root cause, found in `orchestration_stack.py`: the `InvokeAgent`
`LambdaInvoke` task builds its `payload` from an *explicit* field
whitelist (`sfn.TaskInput.from_object({"run_id": ..., "user_id": ...,
"question": ...})`), not a pass-through of the full execution state --
`persona` simply wasn't in that list, so it never reached
`agent_task.py`'s event at all, regardless of being present in the
execution's top-level input. This is the exact same class of bug this
file already has a named regression test for
(`test_narrator_backend_flows_through_invoke_agent_and_into_record_success`,
from an earlier phase's independent review) -- an explicit Step
Functions payload mapping is a second, easy-to-forget place a field has
to be added, separate from wherever it's produced. Unit-testing
`agent_task.handler` directly (as most of this project's Lambda tests
do) cannot catch this class of bug either, since it bypasses the state
machine's own payload-mapping layer entirely -- only a real execution
(or an assertion on the synthesized ASL definition) exercises it. That
same limitation is exactly why this bug survived through unit tests,
`cdk synth`, and CI all the way to a real deployed run before being
caught -- this is a genuine gap in what this project's test suite can
see, not a one-off oversight.

**Decision**: Added `"persona": sfn.JsonPath.string_at("$.persona")` to
`InvokeAgent`'s payload mapping, and a new regression test,
`test_persona_flows_through_invoke_agent_payload`, asserting
`"persona.$" in` the synthesized `InvokeAgent` state's `Parameters
Payload` -- mirroring the existing `narrator_backend` regression test's
exact shape and reasoning, right next to it in the test file.

**Consequence**: All three paths (sync, Step Functions, Queue) now
produce genuinely persona-differentiated answers, confirmed against the
real deployed API, not just against unit tests. The broader lesson: for
this project's Step Functions state machine specifically, any field
added to a Lambda's *call signature* needs a matching addition to that
Lambda's *own* `LambdaInvoke` payload mapping in `orchestration_stack.py`
-- adding it to the execution's top-level input alone (what `start_run.py`
does) is necessary but not sufficient, and neither `cdk synth` nor a
unit test calling the handler directly will catch a mapping that's still
missing it.

---

## 2026-09-21 — Independent review of the V2/Workbench/persona work: 5 findings, all confirmed and fixed

**Context**: An independent review (separate tool, same day, fixed at
commit `a9ed156`) audited the last several entries' worth of work --
V2 tool-calling, the Workbench redesign, and the persona fixes -- from
scratch, against an isolated copy of the repo, with no source changes,
deploys, or paid model calls of its own. It reported 5 findings (2 High,
3 Medium), each with a concrete local reproduction. All 5 were verified
independently against this repo's actual code before any fix, per this
project's standing rule that a finding is acted on because it was
reproduced, not because it was reported. All 5 reproduced exactly as
described and were fixed the same session.

**F1 (High, regression) -- clinician-persona phrasing bypassed the
diagnosis/dosing safety checks.** The clinician system prompt
(`narrator/_prompt.py`) explicitly instructs the model to write "the
patient", never "you" -- but `_DIAGNOSIS_PATTERNS`/`_DOSING_PATTERNS` in
`safety.py` were written entirely around second-person phrasing.
"The patient has diabetes." and "Increase the patient's dose." both
passed `check_no_diagnosis`/`check_no_dosing` outright where "You have
diabetes."/"Increase your dose." correctly failed -- confirmed by
calling the real check functions directly. This wasn't a new class of
prompt-injection risk; it was this project's *own* newly-added persona
feature producing exactly the third-person phrasing its own safety
checks had never been taught to recognize. Fixed by rewriting every
subject-specific pattern as an explicit `(?:you ...|the patient ...)`
alternation rather than a second, separately-maintained pattern list,
so a future third phrasing can't reopen the same gap by being forgotten
in only one of two places. Regression tests added directly against
`check_no_diagnosis`/`check_no_dosing` for every third-person case the
review demonstrated.

**F2 (High, new) -- V2 skipped V1's source-data staleness/plausibility
checks entirely.** `ask()` (V1) always runs `staleness_limitation` and
`implausible_value_limitations`/`assess_plausibility` before building
its `Brief`; `ask_compound()` (V2) built its `Brief` purely from tool
results and never ran either. Confirmed live: with a synthetic sample
panel dated 2020-01-01 and an LDL-C of 5000 mg/dL, V1 correctly flagged
both `stale_data` and `implausible_value` limitations; V2's
`get_marker_snapshot` for the same marker returned the raw value with
`limitations=[]` and `safe=True`. Fixed by extracting the shared logic
into `HealthAgent._apply_source_data_checks(brief, trace, bloodwork)`
and calling it from both `ask()` (unchanged behavior, byte-for-byte --
confirmed by the existing full test suite still passing unmodified) and
`ask_compound()` (new). There is now exactly one place either check
runs, the same discipline this project already applies to the safety
gate itself (`_narrate_and_verify`) -- a third pipeline literally cannot
forget this check without calling something that doesn't exist.

**F3 (Medium, regression) -- the previous entry's persona fix broke
`stress_test.py`'s direct Step Functions entry points.** `InvokeAgent`'s
payload mapping now requires `$.persona` to resolve (added in the
previous entry) -- but `stress_test.py`'s `burst_async` and `race`
commands call `start_execution` directly, bypassing `start_run.py` (the
only place that normally defaults it), so their `input` never carried
`persona` at all. Confirmed by inspecting both call sites directly (not
by launching a real execution against the deployed stack). Fixed by
adding `"persona": "patient"` to both. A full moto-simulated execution
replaying the real synthesized state machine to reproduce the exact
`States.Runtime` failure live was considered and not built -- MarkRunning
(the state before InvokeAgent) would need to actually execute via a real
moto-mocked Lambda invocation to reach the failure point, disproportionate
engineering for what the code fix and the existing
`test_persona_flows_through_invoke_agent_payload` regression test (from
the previous entry) already make correct and provable by inspection.

**F4 (Medium, new) -- `capability_gate` accepted a tool call missing its
required argument, which then crashed instead of being rejected.**
`PlannedToolCall("get_questionnaire_fact", {})` passed `capability_gate`
cleanly (only `get_marker_trend`/`get_marker_snapshot`'s `concept_id` and
`search_knowledge`'s `query` had bespoke presence checks), then
`execute_tool_call` raised a bare `KeyError('field')` from
`args["field"]` -- confirmed directly. This bypassed the planner's own
bounded repair path entirely: a malformed call should produce a
rejection reason the planner can act on, not a Python exception. Fixed
by making `capability_gate` generic over `TOOL_SPECS`'s own declared
parameter names (`_TOOL_PARAMS`, derived from `TOOL_SPECS`, not
hand-listed a second time) -- every declared parameter must be present
and a non-empty string before a call is accepted, for every tool, not
just the two that happened to get individual checks before. A future
tool with a required argument cannot reintroduce this gap by simply
being new.

**F5 (Medium, new) -- a partially-rejected tool plan silently dropped
the rejected half with no disclosure.** When a plan mixed one accepted
and one rejected call, `run_compound_reasoning`'s loop broke as soon as
`accepted` was non-empty (`if accepted or not rejections: break`) --
correct for moving on to execution, but the rejected call's reason was
then simply discarded: `brief.limitations` only ever got populated in
the *all-rejected* branch. Confirmed directly: a plan with one legal
`get_marker_trend` call and one call for an unsupported marker produced
a `Brief` with `limitations=[]`, indistinguishable from a plan that only
ever asked about the legal marker. Fixed by capturing the rejections
from the round that actually broke the loop (`unresolved_rejections`)
and turning each into a `Limitation(kind="partial_tool_rejection", ...)`
on the final `Brief` -- which every narrator already renders (the mock
narrator's `_compose_compound` renders every `Limitation`, and every LLM
narrator's prompt is built from the mock narrator's own rendered text,
so this reaches the real Bedrock-narrated answer too, with no narrator
changes needed).

**What the review also confirmed was solid, not just what it found
broken**: persona now reaches every path correctly (the thing it was
checking); red-flag detection still runs before the planner, unconditionally;
hard/soft severity classification still doesn't loosen the fallback
trigger (any failed check still falls back, regardless of severity); V2
retrieval content (not just citation names) does reach the narrator
prompt; the Workbench's local-only demo tab is correctly hidden in
production; prior fixes (history-clear-on-logout, Markdown image
blocking, poll-generation guarding against out-of-order responses) are
all still in place and weren't re-reported as new.

**Consequence**: All 5 fixes verified against a from-scratch clean venv
matching CI's exact dependency sets for both the core package (`ruff
check/format src tests`, `mypy src`, `pytest -q --cov=care_agent
--cov-fail-under=85` -- 227 passed, 85.75% coverage) and infra (`ruff
check . --line-length=140`, `mypy stacks app.py lambda_src
build_lambda_asset.py scripts/get_dev_token.py scripts/stress_test.py
--ignore-missing-imports`, `pytest tests/ -v`, `cdk synth --quiet` --
173 passed), per the now-standing lesson from two entries ago. Not yet
deployed at the time of this entry.

---

<!-- Template for new entries:

## YYYY-MM-DD — Short decision title

**Context**: What prompted this decision.

**Decision**: What was chosen.

**Alternatives considered**: What else was considered and why it lost.

**Consequence**: What this makes easier/harder going forward.

-->
