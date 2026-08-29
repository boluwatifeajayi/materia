# Per workbook results

`Seeded` counts every mutation, `Material` counts the ones that move a declared output by at least one percent. `FP` is findings that do not correspond to a seeded mutation.

| ID | Role | Seeded | Material | Detectors only found | Detectors only reported | Detectors only FP |
| --- | --- | --- | --- | --- | --- | --- |
| C01 | seeded | 1 | 1 | 1 | 22 | 21 |
| C02 | seeded | 1 | 1 | 1 | 22 | 21 |
| C03 | seeded | 2 | 2 | 2 | 22 | 20 |
| C04 | seeded | 1 | 1 | 1 | 22 | 21 |
| C05 | seeded | 1 | 1 | 1 | 22 | 21 |
| C06 | seeded | 3 | 3 | 3 | 23 | 20 |
| C07 | seeded | 1 | 1 | 1 | 21 | 20 |
| C08 | seeded | 2 | 2 | 2 | 23 | 21 |
| C09 | clean control | 0 | 0 | 0 | 21 | 21 |
| C10 | clean control | 0 | 0 | 0 | 25 | 25 |
| C11 | hard case | 1 | 0 | 1 | 22 | 21 |
| C12 | hard case | 2 | 2 | 1 | 22 | 21 |
