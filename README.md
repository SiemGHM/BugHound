# BugHound

## An agentic system to find the root cause of bugs in codebases and propose fixes as soon as issues are unclosed, with **eval harness** to measure accuracy.

### Early branch with work in progress.

## Planned architecture

- **Tools** — The system will have tools such as `search_code`, `read_file`, `run_tests`, `git_log`, `propose_patch`. These tools will return bounded output, and patches will be proposed as diffs, where we then can choose to apply.
- **Agent loop** — hand-written ReAct loop over the model's native tool-calling API. Keeping track of cost, steps, tokens and time taken logged as JSON.
- **Sandbox** — target repo runs inside Docker image.
- **Evals** — benchmark of real closed issues paired with their fixing commits. Scored on correct file, correct function, and whether the proposed patch makes the failing test pass.

## Roadmap

- [ ] Week 1 — sandbox + tools + loop working end-to-end on one hand-picked issue
- [x] Week 2 — benchmark dataset mined from GitHub issues 
- [ ] Week 2(b) - Get eval Script ready
- [ ] Week 3 — iterate on prompts, tool design, and budgets against the benchmark
- [ ] Week 4 — trace viewer, prompt caching, multi-model comparison
- [ ] Week 5 — deploy demo, write up results and failure modes
