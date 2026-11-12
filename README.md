# context-bench

One stream, one meter, one board for context engines.

A context engine decides which messages of a long chat go to the model at each request. Here, engines run on the
same chats, under the same token budget, with the same meter. The board then puts each pair of engines head to
head, question by question.

- The board: [BOARD.md](BOARD.md)
- The contract for an engine: [contextbench/engine.py](contextbench/engine.py)
- Python 3.11 or later. The harness uses only the standard library. Each open-source engine brings its own
  dependencies, pinned exactly, in its own scratch venv.
- The first board needs no model and no judge. Engines that need a model are under "Next entrants".

There are two places where outside work comes in. Both are pinned exactly.

| Plug-in point | What it holds | Folder | Pinned by |
|---|---|---|---|
| **engines** | what is tested | `engines/<name>/` | `engine.json` `source`: an exact pip or git pin |
| **sources** | what it is tested with | `sources/<name>/` | `source.json`: revision and SHA-256 of each file |

## Quick start

```
git clone https://github.com/breadcrumbs-agency/context-bench && cd context-bench
python -m contextbench fetch sources/locomo --into data       # downloads 2.8 MB and checks its SHA-256
python -m contextbench run engines/window --source locomo --data data/locomo10.json \
    --budgets 4096 8192 16384 --out results/private/locomo-window.json
python -m contextbench board results/private                  # the board of your records, on screen
# an open-source engine comes in through a scratch venv that holds its exact pins:
python -m contextbench fetch engines/langchain-trim --venv /tmp/cb-lc
/tmp/cb-lc/bin/python -m contextbench run engines/langchain-trim --source locomo --data data/locomo10.json \
    --budgets 4096 8192 16384 --out results/private/locomo-langchain-trim.json
```

A run restarts itself with `PYTHONHASHSEED=0`, because some engines break a tie in set order. The same command then
gives the same record. Delete the scratch venv when you are done.

## What the harness does

For each chat of a source, and for each budget, the harness:

1. makes a new engine in an empty folder,
2. sends the chat request by request, in order: one request at each point where the chat calls the model, with the
   whole chat so far (the system message first, the newest message last),
3. when the chat has questions: takes the engine's state once, then asks each question as the new user message at
   the end of the chat, scores the request that the engine sends as the context, and restores the state, so that
   every question sees the state at the end of the chat,
4. counts the tokens of every request, and deletes the folder.

The engine never gets a gold turn, an answer or a score text. No question reaches an engine before the last request
of its chat. The harness does not cut a request: the engine decides what to send, and the harness records the
largest request, the requests over the budget and the tokens over it.

A dialogue source (a chat of people) calls the model at each user message, as Hermes sends them. An agent source
calls it at each user message and after the last tool result of each run of tool results.

## Sources of the first board

| Name | What | Chats | Questions | Source, pinned | License |
|---|---|---|---|---|---|
| `locomo` | long conversations between two people: 272 sessions, 5,882 turns | 10 | 1,981 | [snap-research/locomo](https://github.com/snap-research/locomo) at `cbfbc1d` | CC BY-NC 4.0 |
| `longmemeval-s` | a user and an assistant, about 490,000 characters of history for each question | 419 | 419 | [xiaowu0162/longmemeval-cleaned](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) at `98d7416` | MIT |
| `codex-sessions` | agent sessions with tool calls and tool results (the OpenAI chat format) | 403 | none | [israellaguan/codex-tool-use-trajectories](https://huggingface.co/datasets/israellaguan/codex-tool-use-trajectories) at `69019b2`, `trajectories.train.jsonl` (41 MB) | MIT, as the dataset card declares it |

The repository holds no source text. `fetch` downloads each file at the pinned revision and refuses a file with
another size or SHA-256. `--data PATH` uses a local copy, with the same check.

- `longmemeval-s` keeps the 419 questions of the private bench that came before this one (`ace_bench`): of the 470
  non-abstention questions, those with a gold user turn in an answer session. The sessions go in the order that the
  file gives, not sorted by date, so that the numbers compare with the runs of Nov 9 2026. `--limit N` takes N
  questions by an even stride.
- `locomo`: the first speaker is the user. A question keeps the evidence ids that name a turn (1,981 of 1,986).
  Category 5 (adversarial) has no answer, so it has no `words` score.
- `codex-sessions` has **no questions**. It measures tokens, cache reuse, cost and requests over the budget only.
  The replay moves the cuts of each engine and not the calls of the model: the agent in the data saw its own
  context, not the context of the engine under test. So this source measures **exposure and cost, not behavior**.
  The license is the one on the dataset card; the raw logs came from a third party, whom the card thanks.

## Engines of the first board

| Name | What | Pin | License |
|---|---|---|---|
| `full` | the ceiling: the whole chat at each request. No budget, no rank | in-repo | MIT |
| `window` | the newest messages that fit the budget (port of `ace_bench` Window) | in-repo | MIT |
| `drop` | the same window, cut in steps like ACE, so its prefix stays cached (port of `ace_bench` Drop) | in-repo | MIT |
| `breadcrumbs-ace` | ACE of the breadcrumbs plugin, at its default settings | `breadcrumbs @ git+https://github.com/breadcrumbs-agency/hermes-breadcrumb-plugin@5be1e74…` | MIT |
| `langchain-trim` | langchain-core `trim_messages`, strategy `last`, `include_system`, `start_on` human | `langchain-core==1.8.0` | MIT |
| `llamaindex-buffer` | llama-index-core `ChatMemoryBuffer`, `token_limit` = the budget | `llama-index-core==0.14.25` | MIT |

`full`, `window` and `drop` are the floors and the ceiling. `drop` cuts to 0.6 of the budget: that is the value
of `ace.FOLD_TO` in the runs of Nov 9 2026, which `ace_bench` passed to its Drop (ACE has 0.9 now). The two
open-source engines are pure Python and need no model. Their pins are the newest releases that pip resolved on a Mac
on Nov 11 2026. `requirements.txt` of each engine holds the whole resolved set, pinned.

## The meter

The harness counts. An engine never reports a size, a cost or a score.

- **Tokens.** The size of a request is the sum, over its messages, of the UTF-8 bytes of the text, the tool calls
  and the thinking that go back with the message, plus 16 bytes of framing. A token is 4 bytes. This is the counter
  of `ace_bench`. It is not the tokenizer of a model; it is the same for every engine and needs no download.
- **exact.** The share of the gold turns whose full text (spaces made equal) is in the context. A gold turn is a
  message without the `[date] ` stamp that opens a session. The context is every message of the request but the last,
  which is the question. **words.** The share of the words of the gold answer that the context holds; the words of the
  question do not count.
- **cache_reuse.** Per request: the share of its tokens that sit in the leading messages that the request before
  also sent (the prefix that a provider caches). The first request of a chat has none.
- **cost.** Per request, in two forms. (a) `ace_bench`'s token units: 0.1 for each cached token and 1.25 for each
  other token. (b) Dollars of input from a dated price card in `prices/`: cached tokens at the cache-read price, the
  others at the 5-minute cache-write price. The card of 2026-10-07 holds Claude Haiku 5.5, Sonnet 5.5 and Opus 5.5.
  Haiku 5.5 steps up at 100,000 prompt tokens: a request over it pays the higher prices for all its tokens, and
  cache reads count toward the 100,000. Each record names the date and the SHA-256 of its card. The board shows Sonnet
  5.5 and Haiku 5.5; the records keep all three.
- **Budget.** The harness does not cut. Per run it records the largest request, the requests over the budget and
  the tokens over it, and the board shows them beside each run. A budget is the number of tokens that an engine is
  told to stay under; `full` has none. One LongMemEval-S message is larger than 8,192 tokens, so at that budget
  every engine sends a request of 19,201 tokens once.
- **Not in the shared meter:** `route@k` and `reach` of `ace_bench`. They need the crumb refs of one engine (ACE),
  so they cannot be one rule for all engines.

A change to the stream, the meter or the scoring changes `PROTOCOL` in `contextbench/__init__.py`. The board then
keeps old records in their own group.

## The board

For each source and budget, the board writes a table and, where the source has questions, a head-to-head matrix: the
paired per-question difference of `exact` with a normal 95% interval (mean ± 1.96 · sd / √n). It names an engine ahead
only when that interval excludes zero. Runs share a group only when the protocol, the source bytes (SHA-256), the
budget, the question set and the chat set are the same. `full` is the ceiling row in every group of its source. A
run of a licensed engine shows numbers only with a `publication` grant in its manifest; without one, the record goes
to `results/private/` (git ignores it) and `board --private` reads it.

## Charts

`python -m contextbench plot results/public --out charts/` writes the charts as plain SVG text. It reads the run records
and nothing else, and it needs no plotting library. The bytes depend on the records only (one number format, sorted
series, no date), so a rebuild gives the same files and a gate can compare them. `BOARD.md` shows the charts of a source
under its tables. `--private` also charts the runs that the board withholds.

| File | Shows |
|---|---|
| `charts/<source>-exact.svg` | exact against budget: a line for each engine, the 95% interval of the mean at each point (mean ± 1.96 sd/√n), `full` as a dashed ceiling |
| `charts/<source>-cost.svg` | input cost per request in dollars against budget: a panel at Sonnet 5.5 and a panel at Haiku 5.5 |
| `charts/<source>-tradeoff.svg` | exact against dollars per request at Sonnet 5.5: a mark for each engine and budget, `full` as the ceiling mark |
| `charts/<source>-cache.svg` | cache reuse against budget |
| `charts/<source>-trace.svg` | the tokens of each request of one chat, in order: a panel for each budget, a line for each engine. The chat is the first by id whose largest uncut request is over the largest budget of the run (see below). `drop` and ACE fold in a sawtooth, `window` stays at its cap, and `full` leaves the scale (a note says where) |

A source without questions (codex-sessions) has the cost, cache and trace charts only. Lines of engines that send the same
messages lie on each other (in the codex chat of the first board, row-021, the lines of all five engines lie on each
other for requests 1 to 39, and they split at request 40, where the chat first passes the budget); the table under each
plot lists every engine.

Every chart follows five rules. Each measured axis is linear and starts at zero, at the bottom and at the left. The budget
axis has equal steps and is labeled with the budget values (the budgets double; the steps do not). Each axis label names
its unit. Each point carries its number from the record, in its SVG `<title>` (a tooltip) and in the table under the plot
(visible where GitHub shows the SVG as an image). The tests check them: a golden file, the labels against the record, the
zero of each axis, the equal steps, the 1.96 of the bars.

The trace chart needs one more field in the record. Each run keeps `trace`: the tokens of each request of one chat, on
one line. The harness counts it as it counts every request, and it changes no other number.

A chat that stays under the budget shows nothing: no engine cuts it, so every engine draws the same line. (The first
chat of `codex-sessions` is such a chat.) So the chat is the first by id whose largest uncut request is over the largest
budget of the run. An uncut request is the request as the harness hands it to the engine, before the engine cuts it.
The choice does not depend on the engine, so all engines draw the same chat. When no chat is over the budget, it is the
chat with the largest uncut request (on a tie, the first by id). A run with no budget (`full`) has none to be over, and
it takes the first chat by id.

## The rules: apples to apples

1. **Same bytes.** Every run reads the pinned file.
2. **Same stream.** Each engine gets the same chats, request by request, in the same order.
3. **No peeking.** No engine gets an answer or a gold turn. An engine that was tuned on a source says so in `tuned_on`.
4. **Same meter.** The harness counts the tokens and the cost. No engine reports them.
5. **Everything declared.** The version (an exact pin), the license, each model that the engine calls, and where it
   runs. The adapter code is public, also for a licensed engine.
6. **Like with like.** The board compares two runs only when protocol, source bytes, budget and question set agree.
7. **Head to head.** Question by question, with an interval. Costs and cache reuse stand beside the scores.
8. **A record cannot name a version that did not run.** At init, an adapter compares the installed package with
   `engine.json` and raises on a mismatch. For a git pin it also compares the installed commit.

## Draw in an engine

An engine is three files in `engines/<name>/`:

```
engine.json         the manifest
adapter.py          class Adapter(contextbench.Engine): select, and optionally state, restore, stats, close
requirements.txt    exact pins of the engine and its dependencies; empty for an in-repo baseline
```

The smallest adapter (it is `engines/full`):

```python
import contextbench

class Adapter(contextbench.Engine):
    ceiling = True                       # no budget, no rank

    def select(self, request_messages, budget):
        return request_messages          # the messages to send
```

| Manifest field | Meaning |
|---|---|
| `name`, `version` | the engine, and the exact version that the adapter pins |
| `license` | an SPDX id, or `LicenseRef-<name>` for a closed license |
| `access` | `open-source` (an OSI license) or `licensed` (closed, source-available or hosted) |
| `homepage` | where the engine lives |
| `source` | the exact pin: `dist==version`, `dist @ git+https://…@<40 hex>`, or `in-repo: <path>` |
| `about` | one paragraph: what it does, and how the adapter drives it |
| `models` | each model that the engine calls, for example `{"role": "summarizer", "id": "…", "where": "local"}` |
| `runs_on` | `mac` (no model, no heavy runtime) or `kali` (a model or a heavy runtime: torch, onnxruntime) |
| `tuned_on` | each source that tuned a setting of the engine, and the setting |
| `publication` | `null`, or the grant that permits publication (licensed engines) |

`select(request_messages, budget)` gets the chat so far, as message dicts (`role`, `content`, and for an agent,
`tool_calls` and `tool_call_id`), and returns the list of messages to send. `state()` and `restore(s)` keep the state
of an engine that folds, so that each question sees the end-of-chat state. `stats()` may return counters such as
`{"folds": 3}`.

**How an open-source engine comes in:**

1. Resolve the newest release in a scratch venv and write the exact pins to `requirements.txt` (the engine first,
   then everything it pulled in). Do not add torch or onnxruntime on the Mac: an engine that needs one has
   `runs_on: kali`.
2. At init the adapter calls `self.require()`. It compares `importlib.metadata.version()` with `engine.json`, and for a
   git pin the installed commit with the pinned one.
3. Port the working settings from the engine's own docs. Do not tune them on a source of the board.
4. Run `python -m contextbench fetch engines/<name> --venv DIR`, then run the engine with `DIR/bin/python -m
   contextbench run …` and write the record to `results/public/<source>-<name>.json`.
5. `python -m contextbench board results/public --out BOARD.md`, `python -m contextbench plot results/public --out charts`,
   and the tests.

**Open-source donors** open a pull request with the three files. The maintainers run the engine at the pinned
version and commit the run record. Anyone can run it again with the same command. **Licensed donors** (the vendor or a
licensee): the adapter is public; keys and binaries never go into the repository; the board shows the numbers only
with a `publication` grant.

## Draw in a source

A source is two files in `sources/<name>/`:

```
source.json   name, license, homepage, revision, files (file, url, sha256, bytes), size, about
loader.py     chats(files, limit=0): yields contextbench.Chat objects
```

The url of each file must name the pinned `revision`. A `Chat` is `id`, `messages` (the system message first),
`requests` (the message indexes at which the chat calls the model) and `questions`. A `Question` is `id`, `text`,
`category`, `answer` (or `None`) and `gold` (the message indexes of the gold turns). A source with no `questions` is
measured for tokens, cache reuse, cost and requests over the budget only. Use `contextbench.chats.dialogue_requests`
or `agent_requests` for `requests`. A source must have a permissive license (MIT, Apache-2.0, CC BY 4.0 or similar)
or say what its license forbids. No source text goes into the repository, and no record holds source text.

## Next entrants

No adapter exists yet for these. Each needs a model, so each would run on kali, not on the Mac (the compute policy
of the maintainers). kali was not reachable when this board was written.

| Engine | What it does | What it needs |
|---|---|---|
| Hermes's own context compressor | a model summarizes the older turns | a summarizer model (local or an endpoint); the exact Hermes version; `runs_on: kali` |
| LLMLingua-2 | a small model drops the tokens that carry least information | torch and a downloaded token-classification model; `runs_on: kali` |
| Letta | an agent server where a model manages core and archival memory | the server, a model, a database; `runs_on: kali` |

## Limits and port notes

- bytes / 4 is not the tokenizer of a model. The dollar figures follow the card of one date; they price the bytes of
  the bench, not the tokens that a provider would count.
- The costs assume that a provider caches the leading messages of every request (cache write on the rest). A provider
  with other caching rules has other costs.
- `ace_bench` quirks are kept, so that old and new numbers compare: LongMemEval-S sessions in file order; a gold turn
  is found by its text without the date stamp; gold turns are user turns of answer sessions only; a session id that a
  haystack repeats gives two turns one id, and the later one wins.
- `breadcrumbs-ace` runs as `ace_bench` ran it: default settings, the budget as the target, no `budget_tokens`, no
  model name. ACE lets reading raise its target, with no ceiling unless Hermes gives one, so on tool sessions it can
  send requests over the budget. The board counts them.
- `langchain-trim` with `start_on` human keeps a start at a human message. A chat with one human message at its start
  (an agent session) keeps nothing but the system message once it passes the budget: look at "Newest dropped".
- `drop` starts its window at a user message, as in a dialogue. In an agent session with one user message it keeps only
  the newest message after a cut.
- `llamaindex-buffer`: `ChatMemoryBuffer` is deprecated in this release in favor of `Memory`. The adapter follows the
  brief of the first board.
- LoCoMo is CC BY-NC 4.0 (non-commercial). The repository holds none of its text. Each runner must make sure that their
  use agrees with the license.
- The data of `codex-sessions` is one developer's sessions with one agent. It shows how engines cut real tool
  sessions, not how any engine would have steered them.
- The trace chart draws one chat. When the runs of a source chose different chats (runs at different budgets in
  separate files, or a `full` run on a source whose first chat is under the budget), it draws the runs of the chat with
  the lowest id and leaves out the others; a `full` run on a lower chat leaves out every engine. On the first board all
  runs of a source chose the same chat.

## Port check against `ace_bench`

The ports were checked against `ace_bench`'s record of Nov 9 2026 (bench/results/ace-bench.json of the private repository).
On LoCoMo (4096, 8192, 16384) and LongMemEval-S (8192, 32768, 65536, 419 questions), `full`, `window` and `drop` on this
harness equal the old numbers in every figure that both print: exact, words, context tokens, cache_reuse, cost per
request, requests (2,938 and 102,236), the largest request, and the folds of `drop`. `breadcrumbs-ace` does not. It
runs master ACE (commit 5be1e74) at its default settings, and master differs from Nov 9: `FOLD_TO` is 0.9 (it was 0.6),
and it has small-target shares for budgets up to 16,384, gearbox dials (off by default) and `ace_ceiling` (0 by
default). No run isolated which change moves which number. Nothing was tuned to close or open the gap. On LoCoMo at
8,192 tokens its exact is now 0.2925 (Nov 9: 0.1806); on LongMemEval-S it is 0.3799 at 32,768 tokens (Nov 9: 0.3313) and
0.5824 at 65,536 tokens (Nov 9: 0.5985).
The gap is ACE's change, not the port: `ace_bench` itself, run on the same master ACE (commit 5be1e74), gives the same
figures as `breadcrumbs-ace` here on LoCoMo at 4,096 and 8,192 tokens (exact 0.1368 and 0.2925, folds 466 and 172, cost
per request 1105.3 and 1221.0, largest request 4095.75 and 8190.75).

## Reproduce the first board

```
python -m contextbench fetch sources/locomo --into data
python -m contextbench fetch sources/longmemeval-s --into data            # 277 MB
python -m contextbench fetch sources/codex-sessions --into data           # 41 MB
for e in breadcrumbs-ace langchain-trim llamaindex-buffer; do python -m contextbench fetch engines/$e --venv /tmp/cb-$e; done
# full, window and drop run with any Python 3.11+; the three others with their own venv (/tmp/cb-<name>/bin/python):
python -m contextbench run engines/window --source locomo --data data/locomo10.json --budgets 4096 8192 16384 \
    --runner breadcrumbs-agency --role maintainer --out results/public/locomo-window.json
python -m contextbench run engines/window --source longmemeval-s --data data/longmemeval_s_cleaned.json --budgets 32768 \
    --runner breadcrumbs-agency --role maintainer --out results/public/longmemeval-s-window-32768.json
python -m contextbench run engines/window --source codex-sessions --data data/trajectories.train.jsonl --budgets 32768 \
    --runner breadcrumbs-agency --role maintainer --out results/public/codex-sessions-window.json
python -m contextbench board results/public --out BOARD.md
python -m contextbench plot results/public --out charts
```

`full` has no budget and runs once for each source (`locomo`, `longmemeval-s`); it is not run on `codex-sessions`. On a
12-core Mac that also ran other jobs, `breadcrumbs-ace` took 12 minutes on LongMemEval-S at 32,768 tokens, 9 at 65,536
and 40 at 8,192 (more folds). `langchain-trim` and `llamaindex-buffer` took one to two minutes for each budget, and
`full`, `window` and `drop` about one minute. A run keeps one chat in memory at a time, and the LongMemEval-S file once
(about 1 GB).

## Who keeps the board

breadcrumbs-agency keeps this repository, and it also wrote the `breadcrumbs-ace` entrant and the private bench that
this one grew from. Its runs have the role `maintainer`. `full`, `window` and `drop` are floors and a ceiling: they
show what an agent gets with no context product.

## Tests

```
python -m unittest discover -s tests          # Python 3.11 and 3.14
python tools/mutate.py                        # the mutation pass: one guard string at a time in a scratch copy
```

The mutation pass copies the tree to scratch (without `.git` and `results`), changes one guard string, runs the suite
and expects it to fail. A mutant that lives needs a test. The gate for a change: clone the repository into scratch,
run the tests there, run `python -m contextbench board results/public --out fresh.md` and `cmp fresh.md BOARD.md`, then
`python -m contextbench plot results/public --out fresh-charts` and `diff -r fresh-charts charts`.

## License

The harness and the adapters in `engines/` are MIT ([LICENSE](LICENSE)). Each source and each engine keeps its own
license.
