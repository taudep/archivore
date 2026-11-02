# Docker Agent: HN Thread and Article Summary

- **HN thread:** https://news.ycombinator.com/item?id=49996259 (304 points, 145 comments)
- **Article:** https://github.com/docker/docker-agent
- **Summarized:** 2026-10-11

> This version is based on a full read of all 145 comments (raw JSON from `hn.algolia.com/api/v1/items/49996259`). An earlier version used summarizer digests and contained errors: `sbx` is Docker's CLI, not Anthropic's, and Pullboard's author is `Olscore`.

---

## 1. The article: what Docker Agent is

An agent builder and runtime, shipped as a Docker CLI plugin (`docker agent`) and as a standalone binary. Agents are defined in YAML. The maintainer (`dgageot`) said it began about 1.5 to 2 years ago as **cagent**, "compose for agents", when people started mixing agentic loops and non-agentic parts in compose files. YAML can be replaced by HCL or Go code (there is a Go SDK).

**Main features**
- Multi-agent teams with delegation from a root agent.
- Built-in tools plus any MCP server (local, remote, Docker-based).
- Provider-agnostic: OpenAI, Anthropic, Gemini, Bedrock, Mistral, xAI, Docker Model Runner.
- Built-in `think`, `todo` and `memory` tools.
- RAG: BM25, embeddings, hybrid search, reranking.
- Sharing through OCI registries.
- A sandbox mode that runs it inside a Docker sandbox (a VM, per `gregwebs`).

**Minimal config**
```yaml
agents:
  root:
    model: openai/gpt-5-mini
    description: A helpful AI assistant
    instruction: |
      You are a knowledgeable assistant that helps users.
      Be helpful, accurate, and concise in your responses.
    toolsets:
      - type: mcp
        ref: docker:duckduckgo
```

**Commands:** `docker agent run [file.yaml]`, `docker agent run myorg/agent:tag`, `docker agent new`.

---

## 2. Overall mood

Mostly sceptical.
- **"What does this have to do with Docker?"** (`mplewis`, `speedgoose`, `sergiotapia`). Others said it is chasing trends (`binsquare`, `sleepybrett`) or that "Great tech. Not a business" (`sneak`).
- **Harness glut.** `blakeashleyjr` compared harnesses to JavaScript frameworks. `blutoot` listed five overlapping products (kagent, agent-sandbox, docker-agent, deepagents, Cloudflare sandboxes) and said they could not tell them apart. `reactordev`: "We're all building the same stuff."
- **Differentiation.** `maxdo` asked why bother when modal.com, e2b and Cloudflare exist. `smashah`: "Why can't we set the harness?" `kingcauchy` noted harnesses are released before a good agent is built on them.
- **YAML and "no code".** `ravenstine` and `esafak` dislike YAML. `woah` asked why "no code required" matters when agents write code on demand. `crossroadsguy` replied that YAML needs less mental engagement than code.
- **Brittleness.** `CBLT` found sessions broke repeatedly when Docker Agent controlled Codex through its harness integration; Codex output over 10k characters killed the session. A Docker engineer (`aheritier`) said Codex calling Docker Agent over ACP should work and that using ACP to control other harnesses could be added.
- **Data.** `segmondy`: Docker now collects inputs and outputs, so read the terms.
- **Docs.** `McScrooge` and `ShinyLeftPad` found no security or attack-vector discussion in the sandbox docs.
- **Counter-view.** `ravenstine`: an agent inside a Linux VM can work out container orchestration itself, so most of these docs are unnecessary. `NamlchakKhandro`: "just use and customise Pi."
- **AI-written README.** `tamimio` suspected an AI wrote the README, which led `lukemercado`, `baby_souffle` and `cyanydeez` to a side thread on eroding trust in text.

---

## 3. Techniques for running and building agents

### Fight drift (`Olscore`, Pullboard)
"Orchestration is not really the core problem. The bigger challenge is handling agent coherency over long periods of time (drift)." Pullboard is a forum-like board where agents "shout" to each other, document work in items, and follow **doctrine** from the developer against a **checkable specification**. Used on large projects that "MUST be correct". The README says: "Nothing ships until a second agent verifies it." When its own CI badge showed a failing gate, the author explained that one agent fixes, another verifies, then it merges. `gerwim` (ForgeBoard) asked how agents find the right posts and handle changing specs; no answer.

### Make "done" executable (`smashed`, egzo)
Trying to fix "agent said work was done, it was not". Work is defined first as runnable specs (a pytest suite), then the change is made. "You have to resist making natural language Todo lists." Specs run against the built binary. Claude Code picked up the system without complaint. Author says it is early.

### Async task tracking beats chat (`pea`)
Chat is great for open-ended work but "terrible at concurrency", i.e. more than one agent run. Cheaper agents mean more bounded processes with many items, hence Jira-like tools. Jira-style tracking also solves multi-player work, which chat does not; async is "the only sane solution". `jredwards` noted Gas Town (Yegge) did the same in January.

### Coordinate by telling the agent the rule (`mgw`, Podium)
Podium has an agent communication system and a Linear-style tracker used by agents and humans. Orchestration is discussed with the agent, which acts through a CLI. Example instruction: "every codex implementer gets an opencode reviewer; when a subtree is done, review with opus high." The system builds the graph and enforces the rules. They found any UI to prescribe coordination too clunky.

### Route instead of chaining (`magnusokik`)
Keeping track of many concurrent agents is exhausting. Use a single orchestration agent that routes to agents by capability, rather than agents invoking agents directly.

### Snapshot every step (`verdverm`)
Their Go harness runs in a Dagger environment; every tool call and file modification creates a new container layer, for session time travel and forking. This is why they want deep integration rather than IPC plugins. Go's plugin story is weak for harness extension (the stdlib `plugin` package needs identical toolchains and flags); TypeScript harnesses can import SDK types, but TS plugins are a security concern.

### Sandboxes and secrets
- `gregwebs`: Docker Agent is a harness; for any other harness (Claude, Codex, Pi) use Docker's **`sbx`** CLI. Fan of secure VMs; building agent-vm.
- `genghisjahn`: sbx's **sentinel value** wrapper. The model never sees the secret; outbound calls are matched on the sentinel and the real value is substituted for the target API. Easy to see what the agent can and cannot reach.
- `gwlthr` wanted easier sharing of whitelisted domains between sandboxes; `genghisjahn` showed the commands: `sbx policy allow network ...`, `sbx policy rm network --resource ...`, `--sandbox` for per-sandbox rules.
- `tonymet`: wants sudo-style ACLs for commands and network credentials scoped by OAuth scope, set before launch, with agents able to request more permission at run time. `Miyamura80` pitched sealgate.ai (MCP as auth middleware with encrypted credentials).

### Keep the reasoning (`ogou`, Backlog.md)
Planned, specced and broke a large project into Backlog.md tasks, recording changes and business-logic issues in each. The finished tasks become a knowledge store "that the git history doesn't handle", with a timestamped record of thought process and experiment results.

### Give agents a CLI and prune context
- `Zambyte`: Atlassian's `jira-cli` made Jira tolerable and easy for AI tooling to use.
- `alemanek`: wrote a session-start hook to prune most of the skills an Atlassian tool (`twg`) loads, keeping only the non-Rovo ones. 15 minutes of setup.

### Reproducible research (`beckford`)
For agent research papers, `uv` gives isolated, repeatable environments and, unlike Docker, runs on Windows and Mac GPUs. Reruns are needed to check that LLM variability isn't causing overconfidence. `agentdev001` pointed to AgentENV for research at scale.

---

## 4. Tools mentioned

| Tool | Poster | Idea |
|---|---|---|
| Pullboard | Olscore | Forum-style board, doctrine, second-agent verification |
| egzo | smashed | Specs as pytest suites |
| Podium | mgw | Agent comms plus Linear-style tracker, rules given in English |
| Epiq | ionetan | Local-first, git-synced, minimal-field issue tracker |
| ForgeBoard | gerwim | Another agent board |
| Backlog.md | ogou | Timestamped task records as knowledge |
| sbx | genghisjahn, gregwebs | Docker's sandbox CLI with sentinel secrets |
| agent-vm | gregwebs | Secure-VM agent runner (in progress) |
| Virtainer | virtainer | Server-side team sandboxes |
| jira-cli | Zambyte | Terminal Jira that agents can drive |
| Gas Town | jredwards | Earlier Jira-for-agents (Yegge) |
| Wingman | ChaseRensberger | Go agent framework with plugins |
| Pi | NamlchakKhandro | Suggested harness to customise |
| AgentENV | agentdev001 | Research-scale agent environments |
| sealgate.ai | Miyamura80 | Credential/command ACLs via MCP |

---

## 5. Side-threads

- **Jira.** Developers who hate Jira now build Jira for agents (`pitched`). The thread's view: people hate their company's configuration (`redhale`, `makeitdouble`), or its slowness and clunky UI (`greysonp`, `devmor`), not issue tracking itself. `pitched` would never use Jira for agents, since an agent can build an equivalent in 10 minutes. `xgb84j`: "You either die a software engineer or you live long enough to see yourself become the project manager."
- **Frameworks.** `yipinwong` said the winner will have the biggest user base regardless of quality; `girvo` replied that early React was easy and got complex after it won. `pianopatrick`: the winner will solve big-tech problems and inherit their complexity.
- **Languages.** `rfgplk` claimed only C++ and Rust are justifiable now that LLMs write safe C++. `triyambakam` agreed that models' ability to write, fuzz and test code changes the language trade-off.
- **Cursor.** Opinions split between `ravenstine` (worst harness) and `disgruntledphd2`, `skylake5` (good review UI, any model).

---

## 6. Takeaways

1. **Drift is the hard problem.** Written doctrine plus a durable work log beats longer prompts.
2. **Executable specs define "done".** Write tests first, avoid prose todo lists, run against the built artifact.
3. **Use a second agent to verify**, ideally from a different model, before anything ships.
4. **Track tasks asynchronously.** Chat doesn't scale to concurrent agents or multiple people.
5. **Route rather than chain** when many agents run at once.
6. **Sandbox and hide secrets.** VM sandboxes, sentinel values, network allow-lists, and scoped runtime permissions.
7. **Snapshot state** (container layers per step) to allow rewind and forking.
8. **Record reasoning** in plain-text task records, not just commits.
9. **Give agents CLIs** and prune the skills and context they load.
10. **Expect framework churn.** Judge any harness on whether you can pick the model and harness, how it breaks on long output, and what it does with your data.

## Open questions
- How do agents find the right posts on a forum-style board as it grows? (Asked, unanswered.)
- What does a good "doctrine" file look like in practice?
- How well do `sbx` sentinel secrets work with non-HTTP credentials?
