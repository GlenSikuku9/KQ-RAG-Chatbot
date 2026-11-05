import { useEffect, useState } from "react";
import {
  adminConversationPage,
  adminDeleteDocument,
  adminDocumentPage,
  adminProcessDocument,
  adminUpdateDocument,
  adminDocumentContent,
  adminSaveDocumentContent,
  adminDocumentVersions,
  adminRestoreDocumentVersion,
  adminUpdateRole,
  adminUpdateStatus,
  adminGenerateModelTest,
  adminSetDefaultModel,
  adminUserPage,
  savePassengerTheme,
} from "../../api";
import {
  formatDate,
  languageLabel,
  metricValue,
  modelDisplayName,
  readableEventName,
} from "../utils";
import "../styles/Models.css";

function ModelManagement({ user, models, evaluations, onChanged }) {
  const [prompt, setPrompt] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [defaultUpdate, setDefaultUpdate] = useState(null);

  useEffect(() => {
    if (!selectedModel && models.length) {
      setSelectedModel(
        models.find((model) => model.is_default)?.model_id ||
          models[0].model_id,
      );
    }
  }, [models, selectedModel]);

  async function testModel() {
    const startedAt = performance.now();
    setTestResult({ loading: true, model_id: selectedModel });
    try {
      const result = await adminGenerateModelTest(user, selectedModel, prompt);
      setTestResult({ ...result, elapsed_ms: performance.now() - startedAt });
    } catch (error) {
      setTestResult({
        error: error.message,
        model_id: selectedModel,
        elapsed_ms: performance.now() - startedAt,
      });
    }
  }

  async function setDefault(model) {
    if (
      !window.confirm(
        `Set ${modelDisplayName(model.model_id)} as the default model?`,
      )
    )
      return;
    setDefaultUpdate({ modelId: model.model_id, status: "loading" });
    try {
      await adminSetDefaultModel(user, model.model_id);
      await onChanged();
      setDefaultUpdate({ modelId: model.model_id, status: "success" });
    } catch (error) {
      setDefaultUpdate({
        modelId: model.model_id,
        status: "error",
        message: error.message,
      });
    }
  }

  return (
    <div className="model-management">
      <section className="admin-analytics-card model-test-panel">
        <div className="admin-section-heading">
          <div>
            <h2>Model testing</h2>
            <p>Test a provider without creating a passenger conversation.</p>
          </div>
        </div>
        <label>
          Test prompt
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Enter a test prompt..."
          />
        </label>
        <div className="model-test-controls">
          <label>
            AI model
            <select
              value={selectedModel}
              onChange={(event) => setSelectedModel(event.target.value)}
            >
              {models.map((model) => (
                <option
                  disabled={!model.configured}
                  key={model.model_id}
                  value={model.model_id}
                >
                  {modelDisplayName(model.model_id)}
                </option>
              ))}
            </select>
          </label>
          <button
            className="button primary"
            disabled={!prompt.trim() || !selectedModel || testResult?.loading}
            onClick={testModel}
            type="button"
          >
            {testResult?.loading ? "Testing model..." : "Test model"}
          </button>
        </div>
        {testResult && !testResult.loading && (
          <div
            className={`model-test-result ${testResult.error ? "error" : ""}`}
          >
            <dl>
              <div>
                <dt>Selected model</dt>
                <dd>{modelDisplayName(testResult.model_id)}</dd>
              </div>
              <div>
                <dt>Response time</dt>
                <dd>{(testResult.elapsed_ms / 1000).toFixed(2)} seconds</dd>
              </div>
            </dl>
            {testResult.error ? (
              <p className="notice error">{testResult.error}</p>
            ) : (
              <div>
                <strong>Generated response</strong>
                <p>{testResult.text}</p>
              </div>
            )}
          </div>
        )}
      </section>

      {defaultUpdate?.status === "success" && (
        <p className="notice success">Default model updated successfully.</p>
      )}
      {defaultUpdate?.status === "error" && (
        <p className="notice error">{defaultUpdate.message}</p>
      )}
      <div className="model-card-grid">
        {models.map((model) => (
          <article
            className={`model-card ${model.is_default ? "default" : ""}`}
            key={model.model_id}
          >
            <div className="model-card-heading">
              <div>
                <h2>{modelDisplayName(model.model_id)}</h2>
                <small>{readableEventName(model.provider)}</small>
              </div>
              {model.is_default && (
                <span className="status-badge active">Default</span>
              )}
            </div>
            <p className="model-technical-id">{model.upstream_model}</p>
            <dl className="model-detail-list">
              <div>
                <dt>Supported languages</dt>
                <dd>{model.language_support.map(languageLabel).join(", ")}</dd>
              </div>
              <div>
                <dt>Availability</dt>
                <dd>
                  <span
                    className={`status-badge ${model.configured ? "active" : "failed"}`}
                  >
                    {model.configured ? "Configured" : "Unavailable"}
                  </span>
                </dd>
              </div>
              <div>
                <dt>Usage</dt>
                <dd>
                  {model.usage_count} ({model.usage_percentage}%)
                </dd>
              </div>
              <div>
                <dt>Average response</dt>
                <dd>
                  {model.average_response_ms == null
                    ? "Unavailable"
                    : `${(model.average_response_ms / 1000).toFixed(2)} seconds`}
                </dd>
              </div>
              <div>
                <dt>Successful requests</dt>
                <dd>{model.successful_requests}</dd>
              </div>
              <div>
                <dt>Failed requests</dt>
                <dd>{model.failed_requests}</dd>
              </div>
            </dl>
            {!model.is_default && (
              <button
                className="button secondary"
                disabled={
                  !model.configured || defaultUpdate?.status === "loading"
                }
                onClick={() => setDefault(model)}
                type="button"
              >
                {defaultUpdate?.status === "loading" &&
                defaultUpdate.modelId === model.model_id
                  ? "Updating..."
                  : "Set as default"}
              </button>
            )}
          </article>
        ))}
      </div>

      <section className="admin-table-card evaluation-card">
        <div className="admin-card-heading">
          <h2>Evaluation comparison</h2>
        </div>
        {evaluations.length ? (
          <div className="admin-table-scroll">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Language</th>
                  <th>Faithfulness</th>
                  <th>Answer relevance</th>
                  <th>Context precision</th>
                  <th>Context recall</th>
                  <th>Sample</th>
                  <th>Run</th>
                </tr>
              </thead>
              <tbody>
                {evaluations.map((item) => (
                  <tr key={`${item.run_id}-${item.model_id}-${item.language}`}>
                    <td>{modelDisplayName(item.model_id)}</td>
                    <td>{languageLabel(item.language)}</td>
                    <td>{metricValue(item.faithfulness)}</td>
                    <td>{metricValue(item.answer_relevance)}</td>
                    <td>{metricValue(item.context_precision)}</td>
                    <td>{metricValue(item.context_recall)}</td>
                    <td>{item.sample_size}</td>
                    <td>
                      <small>
                        {item.dataset_id}
                        <br />
                        {formatDate(item.evaluated_at)}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="admin-empty-state evaluation-empty">
            <h3>No evaluation results yet</h3>
            <p>
              Persist a real evaluation run before comparing quality metrics.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
export default ModelManagement;
