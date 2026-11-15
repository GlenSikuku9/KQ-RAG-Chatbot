# RAG evaluation

The project keeps custom retrieval metrics separate from official RAGAS
metrics. Passenger requests never trigger evaluation or evaluator API calls.

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

## Running official RAGAS metrics

Configure a separate judge in `backend/.env`:

```text
RAGAS_EVALUATOR_MODEL=your-evaluator-model
RAGAS_EVALUATOR_API_KEY=your-evaluator-key
RAGAS_EVALUATOR_BASE_URL=https://api.groq.com/openai/v1
RAGAS_EVALUATOR_TIMEOUT_SECONDS=60
```

The evaluator key and model may be left empty to reuse the configured
`GROQ_API_KEY` and `GROQ_MODEL`. Values such as `your-evaluator-model` are
documentation placeholders and are treated as unset.

Then explicitly run:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_ragas.py `
  --input data\evaluation\model-results.json `
  --output data\evaluation\ragas-results.json `
  --persist
```

If a run is interrupted or partially rate-limited, rerun it with `--resume`.
Successful judgments in the output and checkpoint are reused, while only
failed or unfinished judgments are sent to the evaluator again. Progress is
printed after every judgment.

Omit `--persist` to keep the output local. This command can incur evaluator API
usage. It uses RAGAS 0.4.4 collections metrics: `ContextPrecision`,
`ContextRecall`, `Faithfulness`, and `AnswerRelevancy`.

Context metrics run once per unique case and context fingerprint. Faithfulness
and Answer Relevance run once per generated model answer. Unsupported cases are
reserved for Fallback Accuracy. Reference-dependent context metrics include
only cases whose `ragas_reference_status` is `validated`.

Firestore receives metric scores, case IDs, model/language labels, context
fingerprints, evaluator provenance, status, and timestamps. Full questions,
answers, and retrieved policy chunks remain in the local report only.

