# AI usage log

Honest and specific. Feeds the "AI use" section of DESIGN.md.

| What | AI did | My call / override |
|---|---|---|
| Initial scaffold (agent, tools, eval, loop, docs) | Claude Code wrote the first version from my approved plan | Plan decisions were mine: raw SDK not a framework, guardrails in code not prompt, loop edits only `lessons.json` |
| Simulator ending mid-turn (`<END>` sent with a final "yes") | Claude spotted it in the first smoke transcript and fixed runner + simulator prompt | Accepted; verified on 2 re-runs |
| Rate limits (Tier 1 daily caps on gpt-4.1) | Claude probed limits via headers, switched defaults to gpt-4o-mini / gpt-4o, added whole-trial retry | Accepted; judge stays a different model from the agent |
| Run 1: improver blamed a guardrail that had worked (`DUPLICATE`) | Claude classified as harness bug, added a "guardrails already in code" section to the improver prompt | Accepted: the improver must know what's enforced or it proposes fixes that already exist |
| Run 1: `search_slots` rejected a `visit_type` arg the model kept sending | Claude proposed accepting it as a no-op | Accepted; a `tool_guardrail` finding applied by a human, exactly the loop's intended path |
| Run 2: 6/6 lessons rejected, mostly for one-trial dips | Claude proposed a noise-aware gate (1 flake tolerated) | I required zero tolerance on emergency/identity/privacy scenarios; added `critical` flags |
| `book_happy` persona accepted Dr. Kim when the check demanded Patel | Claude tightened the persona | Accepted: that was a scenario bug, not an agent failure, and was logged as such |
| `relative_date` still failing after 2 prompt attempts | Claude recommended a code-level date resolver rather than more prompt rules | Left open deliberately; documented in DESIGN.md instead of forcing a pass |
| `--baseline` / `--targets` flags | Claude added to reuse a saved baseline and scope fixes | Budget-driven ($5 credit); noted so the demo run's provenance is clear |
