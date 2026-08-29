# Per workbook results

`Seeded` counts every mutation, `Material` counts the ones that move a declared output by at least one percent. `FP` is findings that do not correspond to a seeded mutation.

| ID | Role | Seeded | Material | Detectors only found | Detectors only reported | Detectors only FP | Baseline agent found | Baseline agent reported | Baseline agent FP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C01 | seeded | 1 | 1 | 1 | 22 | 21 | 0 | 0 | 0 |
| C02 | seeded | 1 | 1 | 1 | 22 | 21 | 1 | 1 | 0 |
| C03 | seeded | 2 | 2 | 2 | 22 | 20 | 0 | 0 | 0 |
| C04 | seeded | 1 | 1 | 1 | 22 | 21 | 1 | 1 | 0 |
| C05 | seeded | 1 | 1 | 1 | 22 | 21 | 1 | 1 | 0 |
| C06 | seeded | 3 | 3 | 3 | 23 | 20 | 3 | 3 | 0 |
| C07 | seeded | 1 | 1 | 1 | 21 | 20 | 1 | 1 | 0 |
| C08 | seeded | 2 | 2 | 2 | 23 | 21 | 2 | 2 | 0 |
| C09 | clean control | 0 | 0 | 0 | 21 | 21 | 0 | 0 | 0 |
| C10 | clean control | 0 | 0 | 0 | 25 | 25 | 0 | 1 | 1 |
| C11 | hard case | 1 | 0 | 1 | 22 | 21 | 1 | 1 | 0 |
| C12 | hard case | 2 | 2 | 1 | 22 | 21 | 1 | 1 | 0 |
