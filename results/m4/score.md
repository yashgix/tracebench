# App investigation shop-app-v1

suite `5fd389e9d860` · shop `bfc0fd2` · replaykit 0.3.0 · seeds 1, 2 · limits 15 turns, 25 tool calls, 200000 tokens, 600000 ms · prompt `1e6b1b5cd93c`

## Decisions

| investigator | kind | resolved | right decision | escalated | false escalations | dismissed | false dismissals |
| --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku-5-5 | model | 5/12 | 10/12 | 2/2 | 1 | 1/2 | 0 |
| deepest_abnormal_commit | baseline | 0/12 | 0/12 | 0/2 | 0 | 0/2 | 0 |
| deepseek-flash | model | 5/12 | 6/12 | 0/2 | 0 | 0/2 | 0 |
| gpt-6-luna | model | 5/12 | 8/12 | 0/2 | 0 | 2/2 | 0 |
| revert_last_deploy | baseline | 4/12 | 10/12 | 2/2 | 2 | 0/2 | 0 |

## Diagnosis (incidents that need a fix)

| investigator | kind | root cause | file and function | evidence valid | invalid evidence |
| --- | --- | --- | --- | --- | --- |
| claude-haiku-5-5 | model | 8/8 | 8/8 | 12/12 | 0 |
| deepest_abnormal_commit | baseline | 8/8 | 2/8 | 12/12 | 0 |
| deepseek-flash | model | 6/8 | 6/8 | 6/12 | 0 |
| gpt-6-luna | model | 7/8 | 7/8 | 11/12 | 1 |
| revert_last_deploy | baseline | 6/8 | 2/8 | 12/12 | 0 |

## Remediation (validated on the trusted side)

| investigator | kind | patches | applies | own tests | protected checks | replay clean | valid fix | patch not needed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-haiku-5-5 | model | 7 | 4 | 4 | 2 | 4 | 2/8 | 0 |
| deepest_abnormal_commit | baseline | 0 | 0 | 0 | 0 | 0 | 0/8 | 0 |
| deepseek-flash | model | 6 | 5 | 5 | 5 | 5 | 5/8 | 0 |
| gpt-6-luna | model | 8 | 6 | 6 | 3 | 3 | 3/8 | 2 |
| revert_last_deploy | baseline | 8 | 8 | 8 | 4 | 6 | 4/8 | 0 |

Baselines (deepest_abnormal_commit, revert_last_deploy) are rules, not models: they read every event and all code in the bundle without tools, and their thresholds were picked for this simulator. Neither names a function, so neither resolves a fix inside a function; compare them on root cause and on valid fixes.

## Effort

| investigator | kind | mean turns | mean tool calls | tokens | cost usd | latency s |
| --- | --- | --- | --- | --- | --- | --- |
| claude-haiku-5-5 | model | 5.3 | 10.9 | 376613 | 0.0511 | 209.8 |
| deepest_abnormal_commit | baseline | 0.0 | 0.0 | 0 | 0.0000 | 0.0 |
| deepseek-flash | model | 6.5 | 13.4 | 405861 | 0.1386 | 778.2 |
| gpt-6-luna | model | 6.5 | 16.5 | 316581 | 0.0286 | 272.8 |
| revert_last_deploy | baseline | 0.0 | 0.0 | 0 | 0.0000 | 0.0 |

## Each incident

| incident | investigator | expected | decision | service | commit | root cause | valid fix | resolved |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| shop-app:c1:1 | claude-haiku-5-5 | patch | patch | inventory | ee7027c | True | False | False |
| shop-app:c1:1 | deepest_abnormal_commit | patch | diagnose | inventory | ee7027c | True | - | False |
| shop-app:c1:1 | deepseek-flash | patch | - | - | - | False | - | False |
| shop-app:c1:1 | gpt-6-luna | patch | patch | inventory | ee7027c | True | False | False |
| shop-app:c1:1 | revert_last_deploy | patch | patch | inventory | ee7027c | True | True | False |
| shop-app:c1:2 | claude-haiku-5-5 | patch | patch | inventory | ee7027c | True | False | False |
| shop-app:c1:2 | deepest_abnormal_commit | patch | diagnose | inventory | ee7027c | True | - | False |
| shop-app:c1:2 | deepseek-flash | patch | patch | inventory | ee7027c | True | False | False |
| shop-app:c1:2 | gpt-6-luna | patch | patch | inventory | ee7027c | True | False | False |
| shop-app:c1:2 | revert_last_deploy | patch | patch | inventory | ee7027c | True | True | False |
| shop-app:c2:1 | claude-haiku-5-5 | patch | patch | web | 268076a | True | True | True |
| shop-app:c2:1 | deepest_abnormal_commit | patch | diagnose | web | 268076a | True | - | False |
| shop-app:c2:1 | deepseek-flash | patch | patch | web | 268076a | True | True | True |
| shop-app:c2:1 | gpt-6-luna | patch | diagnose | web | 268076a | True | - | False |
| shop-app:c2:1 | revert_last_deploy | patch | patch | web | 268076a | True | True | True |
| shop-app:c2:2 | claude-haiku-5-5 | patch | diagnose | web | 268076a | True | - | False |
| shop-app:c2:2 | deepest_abnormal_commit | patch | diagnose | web | 268076a | True | - | False |
| shop-app:c2:2 | deepseek-flash | patch | patch | web | 268076a | True | True | True |
| shop-app:c2:2 | gpt-6-luna | patch | patch | web | 268076a | True | True | True |
| shop-app:c2:2 | revert_last_deploy | patch | patch | web | 268076a | True | True | True |
| shop-app:c3:1 | claude-haiku-5-5 | patch | patch | orders | a2a4dc4 | True | True | True |
| shop-app:c3:1 | deepest_abnormal_commit | patch | diagnose | orders | a2a4dc4 | True | - | False |
| shop-app:c3:1 | deepseek-flash | patch | patch | orders | a2a4dc4 | True | True | True |
| shop-app:c3:1 | gpt-6-luna | patch | patch | orders | a2a4dc4 | True | True | True |
| shop-app:c3:1 | revert_last_deploy | patch | patch | web | 828689b | False | False | False |
| shop-app:c3:2 | claude-haiku-5-5 | patch | patch | orders | a2a4dc4 | True | False | False |
| shop-app:c3:2 | deepest_abnormal_commit | patch | diagnose | orders | a2a4dc4 | True | - | False |
| shop-app:c3:2 | deepseek-flash | patch | patch | orders | a2a4dc4 | True | True | True |
| shop-app:c3:2 | gpt-6-luna | patch | patch | orders | a2a4dc4 | True | True | True |
| shop-app:c3:2 | revert_last_deploy | patch | patch | web | 828689b | False | False | False |
| shop-app:c4:1 | claude-haiku-5-5 | patch | patch | inventory | 0b1e0de | True | False | False |
| shop-app:c4:1 | deepest_abnormal_commit | patch | diagnose | inventory | 0b1e0de | True | - | False |
| shop-app:c4:1 | deepseek-flash | patch | - | - | - | False | - | False |
| shop-app:c4:1 | gpt-6-luna | patch | patch | inventory | 0b1e0de | True | False | False |
| shop-app:c4:1 | revert_last_deploy | patch | patch | inventory | 0b1e0de | True | False | False |
| shop-app:c4:2 | claude-haiku-5-5 | patch | patch | inventory | 0b1e0de | True | False | False |
| shop-app:c4:2 | deepest_abnormal_commit | patch | diagnose | inventory | 0b1e0de | True | - | False |
| shop-app:c4:2 | deepseek-flash | patch | patch | inventory | 0b1e0de | True | True | True |
| shop-app:c4:2 | gpt-6-luna | patch | diagnose | inventory | 0b1e0de | False | - | False |
| shop-app:c4:2 | revert_last_deploy | patch | patch | inventory | 0b1e0de | True | False | False |
| shop-app:c5:1 | claude-haiku-5-5 | escalate | escalate | inventory | bfc0fd2 | False | - | True |
| shop-app:c5:1 | deepest_abnormal_commit | escalate | diagnose | inventory | bfc0fd2 | False | - | False |
| shop-app:c5:1 | deepseek-flash | escalate | - | - | - | False | - | False |
| shop-app:c5:1 | gpt-6-luna | escalate | patch | inventory | bfc0fd2 | False | False | False |
| shop-app:c5:1 | revert_last_deploy | escalate | escalate | - | - | False | - | True |
| shop-app:c5:2 | claude-haiku-5-5 | escalate | escalate | inventory | - | False | - | True |
| shop-app:c5:2 | deepest_abnormal_commit | escalate | diagnose | inventory | bfc0fd2 | False | - | False |
| shop-app:c5:2 | deepseek-flash | escalate | - | - | - | False | - | False |
| shop-app:c5:2 | gpt-6-luna | escalate | patch | inventory | bfc0fd2 | False | False | False |
| shop-app:c5:2 | revert_last_deploy | escalate | escalate | - | - | False | - | True |
| shop-app:c6:1 | claude-haiku-5-5 | dismiss | dismiss | web | - | False | - | True |
| shop-app:c6:1 | deepest_abnormal_commit | dismiss | diagnose | web | bfc0fd2 | False | - | False |
| shop-app:c6:1 | deepseek-flash | dismiss | - | - | - | False | - | False |
| shop-app:c6:1 | gpt-6-luna | dismiss | dismiss | - | - | False | - | True |
| shop-app:c6:1 | revert_last_deploy | dismiss | escalate | - | - | False | - | False |
| shop-app:c6:2 | claude-haiku-5-5 | dismiss | escalate | web | - | False | - | False |
| shop-app:c6:2 | deepest_abnormal_commit | dismiss | diagnose | web | bfc0fd2 | False | - | False |
| shop-app:c6:2 | deepseek-flash | dismiss | - | - | - | False | - | False |
| shop-app:c6:2 | gpt-6-luna | dismiss | dismiss | - | - | False | - | True |
| shop-app:c6:2 | revert_last_deploy | dismiss | escalate | - | - | False | - | False |

## Findings

- Most resolved: claude-haiku-5-5, 5 of 12.
- Cheapest per resolved incident: gpt-6-luna, $0.0057.
- claude-haiku-5-5: root cause 8 of 8, valid fix 2 of 8, 7 patches proposed.
- deepest_abnormal_commit: root cause 8 of 8, valid fix 0 of 8, 0 patches proposed.
- deepseek-flash: root cause 6 of 8, valid fix 5 of 8, 6 patches proposed.
- gpt-6-luna: root cause 7 of 8, valid fix 3 of 8, 8 patches proposed.
- revert_last_deploy: root cause 6 of 8, valid fix 4 of 8, 8 patches proposed.
- gpt-6-luna proposed a patch in 2 incidents that needed none.
- claude-haiku-5-5 escalated 1 incidents it should have handled.
- revert_last_deploy escalated 2 incidents it should have handled.
- gpt-6-luna cited evidence it had not seen in 1 incidents.
- deepseek-flash got no answer from its model in 3 incidents (lane stopped or error).
