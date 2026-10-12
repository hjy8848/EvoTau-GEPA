# Observed native repair: Retail task 22

This case supports a narrowly scoped native task repair, not a policy or safety vulnerability repair. The frozen native scorer returned 0 before and 1 after the Service prompt change for both observed simulation seeds (101 and 202). The pending-order address used `New York` before and `NY` after; the database comparison changed from false to true. All four automatic fidelity reviews accepted the Customer behavior and reported no Service policy violation.

| Seed | Phase | Native reward | DB match | Pending-order state argument | Episode key |
|---:|---|---:|---|---|---|
| 101 | before | 0 | False | New York | `b3395d6dd60e212e763b1d990f317c5fd557bf3bcc6e54430addfb73a6731292` |
| 101 | after | 1 | True | NY | `e069d51895987b4556665b391c276f6612f5ced10b483d8eb4f4d3b9828442e5` |
| 202 | before | 0 | False | New York | `4a140268bacb9f900af077beb63a7269e7662e09583038e803ea028bcb2f6e57` |
| 202 | after | 1 | True | NY | `f144bf0b71a807b32d6ece4e982af87ba7ef399d34262254e23328f07da45dd8` |

The official scorer, task and database were unchanged. The Service overlay was rewritten in full and natural conversations differed, so these observations do not isolate one causal instruction. Repeated native success is evidence for this task-format effect; it does not establish general policy compliance or robustness. The repair feedback retains the observed task and seed scope. A Free/RC comparison conditioned on this pair can examine native failure discovery after this particular repair, but cannot establish a broad advantage after policy vulnerability repair.

Exact trajectories are stored as `results/episodes/<episode key>.json`; the attributed prompt diff and fidelity reviews are in `results/repairs/M4-free-43-1.json`.
