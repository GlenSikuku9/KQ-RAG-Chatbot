# RAG evaluation

The project keeps retrieval evaluation separate from passenger requests.

## Preparing a model report

Run the existing model comparison first. This retrieves each question once,
records the exact ordered context used for generation in the local JSON file,
and evaluates each configured passenger model against that shared context.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_models.py `
  --output data\evaluation\model-results.json
```

This command calls the selected passenger-facing model providers. Do not use
`--persist` unless the resulting custom aggregate records should be stored.
