# RL Textbook v1 Author Review Notes

## Scope
- Authored only under evals/rl_textbook_v1/author/.
- Generated the initial book.md and author-manifest.json from the same authored data structure; later synchronization edits kept question records and Markdown aligned without using importer output.
- No API calls, no production code changes, and no real evaluation run.

## Conceptual Assumptions
- Discounted-return convention uses G_t=R_{t+1}+gamma R_{t+2}+gamma^2 R_{t+3}+... ; chapter 7 REINFORCE derivation is explicitly finite-horizon gamma=1 unless stated otherwise.
- Terminal next-state value is treated as 0 unless a question explicitly states otherwise.
- Bellman expectation equations evaluate a fixed policy; Bellman optimality equations use max over actions.
- SARSA targets the actually sampled next action and is on-policy; Q-learning targets max over next actions and is off-policy.
- DQN examples assume discrete actions, target network detachment, and done masking for terminal samples.
- Policy-gradient baseline is assumed action-independent conditional on state, so it changes variance but not expected gradient.
- Off-policy evaluation assumes support overlap: target-policy actions must have nonzero behavior-policy probability.

## Numeric Recomputation Checklist
- RL01Q05: 2 + 0.5*2 + 0.25*2 = 3.5.
- RL02Q05: 1 + 0.9*5 = 5.5.
- RL02Q06: 0.25*2 + 0.75*6 = 5.0.
- RL03Q05: max(0.2+0.9*4, 1+0.9*2) = max(3.8, 2.8) = 3.8.
- RL04Q05: (2+4+6)/3 = 4.
- RL04Q06: the 10th incremental mean sample uses denominator 10.
- RL04Q07: unique greedy action and uniform exploration over all 4 actions gives 1-0.2+0.2/4 = 0.85.
- RL05Q05: 1 + 0.9*4 - 3 = 1.6.
- RL05Q06: 0 + 0.9*5 = 4.5.
- RL05Q07: -1 + 0.5*6 = 2.
- RL06Q05: 1*3 + 2*4 = 11.
- RL06Q06: 1 + 0.99*8 = 8.92.
- RL07Q05: exp(ln3)/(exp(0)+exp(ln3)) = 3/4 = 0.75.
- RL07Q06: 5 - 3 = 2.
- RL08Q05: unique greedy action and uniform exploration over all 5 actions gives 1-0.1+0.1/5 = 0.92.
- RL08Q06: 0.4/0.2 = 2.
- RL08Q07: 10 - 1.96*2 = 6.08.

## Rubric Fairness Notes
- Each short-answer prompt names the eight requested facets, so hidden requirements are avoided.
- Each short-answer reference answer contains eight independently assessable facts.
- The solution for every short answer states the intended basic/advanced/complete weights explicitly.
- Equivalent wording should receive credit when it satisfies the same fact without relevant contradiction.
- Choice questions use one correct option and plausible distractors focused on one misconception.
- Fill-blank questions use exact JSON answer keys; numeric blanks include numeric_value and numeric_tolerance.

## Remaining Ambiguities
- The line-count target was interpreted as physical newline-delimited lines in book.md only, not manifest or notes.
- The phrase “existing meta questions” was interpreted as authored source questions embedded in the textbook, not generated variants.
- The chapter body target was interpreted as pre-question teaching material per H1 before the final meta question H2.
