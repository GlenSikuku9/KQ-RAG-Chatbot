# KQ-RAG-Eval-200 — dataset design and evaluation protocol

## 1. Purpose and composition

KQ-RAG-Eval-200 is a source-grounded, domain-specific benchmark for the Kenya Airways multilingual retrieval-augmented customer support chatbot. It contains 200 newly authored enquiries: 67 English, 67 Kiswahili and 66 English–Kiswahili code-switched. Of these, 180 are answerable from the supplied static policies and 20 assess behaviour when clarification (10) or fallback (10) is expected. Fifteen answerable questions include fixed two-message conversation histories. Twenty matched scenarios each have three language variants (60 questions); the remaining 140 questions have individual scenario identifiers. There are 160 semantic groups in total. The language variants within a group test the same underlying policy intent.

The dataset uses the 14 available English DOCX policy snapshots as the authoritative sources. Kiswahili translations are retrieval representations, not a second independent authority. The 14 translated documents were not all inspected or checked byte-for-byte against the active corpus. Reference answers are canonical English semantic rubrics, not exact strings the chatbot must produce; answers should convey the same facts in the user's language. The questions do not evaluate real-time OAG flight lookups or reservation transactions. Those functions require separate evaluation. Unsupported transaction requests were retained only as behaviour tests.

## 2. Annotation and provenance

Questions, reference answers and required evidence criteria were prepared with AI assistance from the accessible extracted English policy text. The reference answers were paraphrased rather than copied as long passages. Each evidence-bearing record includes the original document name, section, short text anchor, official source URL and mandatory semantic criteria. Some questions have additional short search anchors, and cross-document questions identify the relevant sources. The question count, language balance, unique IDs, semantic-group structure, coverage of all 14 documents, presence of evidence for every answerable question and fixed histories for follow-ups were checked programmatically. Exact normalized question duplicates with the available earlier 60-question proposal were excluded. The available development results were also inspected for earlier intents; exhaustive semantic deduplication against every local evaluation file was not possible.

No independent annotation approval is required by this dataset. The annotations are provided in completed source-grounded form, not as an empty annotation queue. They have not been independently human-validated. The accessible text snapshots could not be materialized as original DOCX bytes, so the active corpus version hashes and complete exact anchor matches were not verified. Therefore, the evidence labels must be resolved against the actual local corpus before numeric retrieval scores can be interpreted. That is a technical evidence-mapping step, not a human approval gate. Resolve ambiguous or inconsistent source passages using the authoritative snapshot and flag unresolved cases explicitly; do not fabricate ground truth or silently exclude difficult questions. Do not treat the benchmark's source snapshots as current operational travel advice.

## 3. Dataset files and schema

- `KQ-RAG-Eval-200.json` is the canonical interchange dataset; `cases` holds all 200 records.
- `KQ-RAG-Eval-200.xlsx` is a human-readable companion with a summary, cases and source register.
- `KQ-RAG-Eval-200.csv` is a simple flat export for inspection and compatibility.

Important fields: `id`, `partition`, `semantic_group`, `language` (`en`, `sw`, `mixed`), `type`, `category`, `question`, `fixed_history`, `answerable`, `expected_behavior` (`answer`, `clarify`, `fallback`), `reference_answer`, `required_evidence` (criterion objects with evidence sources), `expected_source_documents`, `source_locators`, and `retrieval_metrics_applicable`. `source_locators` contain document, section, anchor text and URL. Anchors are search cues, not necessarily complete criterion evidence. `additional_search_anchors` may help locate facts in a multi-part policy. The evidence locators refer to authoritative policy content rather than generated chunk IDs. The evaluation uses the single fixed retrieval configuration specified below. It does not contain fabricated parent chunk IDs or assert exact compatibility with the existing evaluator. Adapt the loader to the project's actual schema; map the expected behaviour names to its corresponding decision classes.

## 4. Fixed RAG configuration

Use the development-selected 1,800-character target and 360-character maximum overlap (20% nominal overlap) with selective bilingual retrieval for the entire final evaluation. The structure-aware splitter uses a maximum overlap rather than guaranteeing exactly 360 repeated characters at every boundary. No alternative chunking configuration or retrieval ablation is required for this evaluation.

| Parameter | Fixed setting |
|---|---|
| Knowledge base | 14 English policy documents and 14 aligned Kiswahili versions |
| Chunking strategy | Structure-aware |
| Target chunk size | 1,800 characters |
| Maximum overlap | 360 characters (20%) |
| Embedding model | `intfloat/multilingual-e5-small` |
| Vector database | ChromaDB |
| Retrieval method | Hybrid retrieval |
| Language routing | Selective bilingual retrieval |
| Reciprocal Rank Fusion constant | 10 |
| Top-K | 5 unique authoritative parents |
| Context supplied to generation | Authoritative English policy text |

Before scoring, verify these settings in the actual implementation, record the code revision, document versions, model identifiers, candidate depth, confidence threshold, context budget, preprocessing rules and prompt settings. Build or load the fixed evaluation index and verify that the bilingual representations share the correct authoritative parent identities. Do not alter the agreed settings in response to held-out results. Record and resolve any implementation discrepancies before evaluation.

Resolve each mandatory evidence criterion against the authoritative parent chunks in this index before retrieval scoring. Accept equivalent supporting passages where the complete criterion is supported; do not assign relevance based only on keywords, a broad heading or document identity. If a criterion genuinely requires multiple parents, apply an explicit combined-evidence rule or flag the mapping limitation. Keep mapping independent of retrieval rankings. Do not fabricate chunk IDs. This is a technical mapping step, not a human approval gate.

## 5. Evaluation procedure and metrics

### 5.1 Retrieval and context evaluation (once)

Run the fixed retrieval and context-construction pipeline once over the dataset, preserving each case's fixed conversation history. Calculate the five retrieval/context metrics on the 180 planned evidence-bearing cases only. Do not call generation or OAG APIs during this phase. Save the actual retrieved parent IDs, ranks, scores, selected context, source references, decision, case ID and retrieval timings for controlled replay. Report unresolved mappings, actual metric denominators and the number of applicable cases; do not count inapplicable behaviour-only cases as zero retrieval scores.

| Metric | Definition |
|---|---|
| Precision@5 | Relevant retrieved parents among the top five, divided by five |
| Criterion Recall@5 | Mandatory evidence criteria supported by the top five parents, divided by all mandatory criteria |
| MRR@5 | Reciprocal rank of the first relevant parent within the top five (zero if none) |
| Context Precision | Relevant supplied context parents divided by all supplied context parents |
| Context Recall | Mandatory evidence criteria supported by the actual supplied context, divided by all mandatory criteria |

### 5.2 Comparative LLM evaluation

Evaluate Gemma 4 26B A4B, Gemma 4 31B and GPT-OSS 120B on the same 200 cases per model (600 planned case-model outcomes). Use the same retrieved context, source references, question, fixed conversation history and system instructions for each comparable case. Record actual provider model identifiers and generation settings; do not leak reference answers or evaluation criteria into generation prompts. Preserve deterministic backend clarification and fallback behaviour rather than forcing generation when no model call is required. The number of actual LLM-generated answers may therefore be below 600. Expected behaviour fields remain in the dataset to describe these cases; Fallback Accuracy is not a separate reported metric.

Use a fixed evaluator model distinct from the three candidate LLMs, with the candidate identity hidden from the evaluator. Apply the same scoring rubric to English, Kiswahili and mixed-language answers. Use the authoritative English reference answer and mandatory criteria as semantic rubrics, not exact answer strings. Evaluate factual equivalence across languages, including numerical quantities, conditions, exceptions and completeness. Record evaluator model, prompt, settings and scoring failures. LLM-based semantic judgements are not entirely objective; acknowledge this limitation.

| Metric | Scoring procedure |
|---|---|
| Faithfulness | Supported atomic factual claims divided by all generated factual claims, assessed against the actual supplied context. |
| Answer Relevance | Mean cosine similarity between the original question and evaluator-generated questions reconstructed from the response, using one fixed multilingual scoring embedding model and a consistent protocol across all candidates. Document the model and any noncommittal-answer handling. |
| Answer Precision | TP / (TP + FP), based on semantic factual-claim matching to the reference evidence. |
| Answer Recall | TP / (TP + FN), based on coverage of the mandatory reference facts. |
| Answer Correctness (F1) | 2TP / (2TP + FP + FN), the claim-level harmonic balance of answer precision and recall. |
| Response Time | Measured elapsed time; report model/API generation latency separately from retrieval and end-to-end latency, and distinguish deterministic no-call cases. |
| Failure Rate | Final technical failures divided by attempted cases (or model requests for a separately labelled API failure rate), multiplied by 100. Document retries, timeouts, errors and empty responses. |

For claim scoring, TP denotes correctly expressed required facts, FP denotes incorrect or unsupported factual assertions, and FN denotes omitted mandatory facts. Deduplicate equivalent claims and do not penalize a true, relevant supplementary fact solely for being absent from the short reference answer; verify it against authoritative evidence. Use a consistent one-to-one matching rubric for required facts. Where no factual claims or no applicable evidence exist, report applicability explicitly; do not manufacture scores when denominators are zero. Distinguish answerable cases with an incomplete/empty substantive answer from legitimate clarification or fallback responses. Report all cases and failures; calculate factual metrics on applicable responses and state how technical failures and missed answers affect coverage and denominators. An incorrect factual answer is not itself a technical failure. If using RAGAS or another implementation, document the actual metric variant rather than equating different formulas.

### 5.3 Analysis and reporting

Report retrieval metrics once for the fixed index. For LLMs, report the seven metrics overall and separately for English, Kiswahili and mixed queries, with actual counts and metric applicability. Where informative, report question-type and document-level results. Summarize latency using mean, median and 95th percentile. Report paired differences and semantic-group-level paired bootstrap confidence intervals to account for dependence in matched multilingual triplets. Do not treat a higher point estimate alone as conclusive evidence of superiority. Passenger satisfaction requires separate user testing and is not inferred from this dataset.

## 6. Held-out evaluation boundaries and limitations

Freeze the RAG pipeline and evaluation rubric before running KQ-RAG-Eval-200. Do not use the held-out questions to tune query expansion, chunking, thresholds, prompts or context selection. Compare the three LLMs under the fixed configuration; if a preferred model is selected using these scores, acknowledge model-selection bias rather than presenting the winning score as an independently confirmed post-selection estimate. Record the evaluation date, software versions, provider settings, errors, retries, actual generated-response counts and any evidence-mapping limitations. The dataset is language-balanced by design and is not a representative sample of real passenger enquiry frequency. Source-grounded annotations have not been independently human-validated, but no independent annotation approval step is required. No model performance results were generated during dataset construction.
