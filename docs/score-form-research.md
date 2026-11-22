# Recent league form and exact-score research

The paired prediction entry point supports an independent six-match form candidate for regular club leagues using WCPP-ML-0.1. Each preceding match supplies the team-perspective regulation goals and optional genuine paired xG. The candidate updates the stored Test recent scoring, conceding, points, both-teams-to-score, over-2.5, same-league gap and xG inputs with the original tree and fusion coefficient. Venue history, 90-day history and process indicators retain their original values. The active Test matrix remains the reference in the issued pair.

Train the league-specific Poisson models from archived Understat league responses with the following command.

```shell
python scripts/train_score_form.py --understat-root PATH/TO/understat --out NEW_TRAINING_DIRECTORY
```

Each league fits on 2020/21–2022/23. The 2023/24 season determines the shrunk observed-to-expected ratios for a five-by-five score grid where each axis is 0, 1, 2, 3, or 4 and above. Season 2024/25 selects the calibration strength by exact first score, then three-score hits and log loss. The reserved 2025/26 season supplies the evaluation, using only matches on earlier dates for features. Historical provider publication times are unavailable. Genuine retrospective xG, calibration samples and forward evidence have separate provenance.

For a scheduled fixture, use the normal prediction entry point.

```shell
python runtime/predict_active.py fixture.json --recent-form recent.json --score-form-artifact eng.1.json --out NEW_PAIR.json
```

A fixture can also carry `recent_league_form` or two structured `home_form` and `away_form` objects. The form envelope identifies the fixture and league with `match_id` and `cohort`. Each side identifies its `team_id` and supplies `matches` with unique historical IDs, cohort, regulation basis, kickoff time, observed time, source URL and SHA-256, goals for and against, and optional xG for and against. Separate xG observations carry their own timestamp, URL and hash. The newest six eligible games enter the feature calculation. With snapshot input, the runner checks the explicit artifact or the installed league JSON in `$WCPP_RUNTIME_ROOT/models/score-form-v1/`. It records the fitted artifact hash or the fallback basis and the number of observed games per team. Missing xG falls back to a shrunk recent-goals estimate. Missing histories or unsupported routes have explicit research status.

The `score_form_candidate` contains the refreshed Test, form and equal-weight pooled predictions. All three use the same actual Asian total and strict full-win objective when the quote qualifies. The candidate considers every score cell. It accepts a proposed first score that at least one of the two experts ranked first, otherwise it uses the pooled mode. Its remaining two scores maximize remaining pooled probability mass. A second comparison reorders only the originally issued three scores, which preserves their coverage and the independently issued WDL and totals directions. The comprehensive freeze records both alternatives. The selection review counts the first scores gained and lost against the original, preserving pending fixtures until regulation results arrive.
