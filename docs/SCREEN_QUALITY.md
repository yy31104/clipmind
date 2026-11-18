# Screen quality

ClipMind v1.6.0 reached the three screen-quality targets on both backends
in round 3. The four complete recordings run for 70 minutes and 28 seconds.
Targets are 85% recall, 50% precision and at most 15% code character error
rate (CER), pooled by backend.

| Frozen round | Backend | Change recall | Boundary precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| 1 | Mac / Vision | 168/211 (79.62%) | 168/894 (18.79%) | 1445/3734 (38.70%) |
| 1 | Docker / Tesseract | 162/211 (76.78%) | 162/931 (17.40%) | 1576/3734 (42.21%) |
| 2 | Mac / Vision | 128/139 (92.09%) | 128/608 (21.05%) | 940/6980 (13.47%) |
| 2 | Docker / Tesseract | 129/139 (92.81%) | 129/582 (22.16%) | 2695/6980 (38.61%) |
| 3 | Mac / Vision | **154/174 (88.51%)** | **154/274 (56.20%)** | **171/1675 (10.21%)** |
| 3 | Docker / Tesseract | **154/174 (88.51%)** | **154/271 (56.83%)** | **131/1675 (7.82%)** |

Each round used different recordings and ran once per backend. Rounds
1 and 2 missed their targets and moved into development. The final
candidate completed all four recordings on both backends. Extraction
took 5.1 minutes on Mac and 13.8 minutes in Docker with a warm local
model cache.

Inkscape and C/Make still produced many extra boundaries. Docker
captured 12 of 20 Inkscape changes. The AppImage slide talk supplied
69 of 174 reference changes and was the strongest recording. Round 3
code CER covers 15 regions from two recordings.

- [Round 3 results and raw run records](HOLDOUT_V160_ROUND3_RESULTS.md)
- [Round 3 sources, labels and independent agreement](SCREEN_V160_ROUND3.md)
- [Round 1 results](HOLDOUT_V160_RESULTS.md) and [round 2 results](HOLDOUT_V160_ROUND2_RESULTS.md)
- [Development comparisons on matching inputs](SCREEN_V160_DEVELOPMENT.md)
- [v1.5.0 results](SCREEN_QUALITY_V150.md) and [scores with revised labels](SCREEN_REFERENCE_COMPARISON.md)
- [Published Docker timing for the 39-minute Xous talk](HOLDOUT_EXECUTION.md) and [other tools](TOOL_COMPARISON.md)
