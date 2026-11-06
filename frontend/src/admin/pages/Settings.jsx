import { useEffect, useState } from "react";
import { savePassengerTheme } from "../../api";
import { KenyaAirwaysLogo } from "../../components/KenyaAirwaysLogo";
import "../styles/Settings.css";

function ThemeSettings({ user, theme, setTheme, onSaved }) {
  const [formTheme, setFormTheme] = useState(theme);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    // Refresh untouched settings from the server without replacing unsaved
    // colour edits currently being reviewed by the administrator.
    if (!dirty && theme) setFormTheme(theme);
  }, [dirty, theme]);

  // Group the existing persisted tokens for usability; storage keys and the
  // passenger-only settings API remain unchanged.
  const groups = [
    {
      title: "Branding",
      description: "Core navigation, headings and accent actions.",
      fields: [
        ["primary", "Primary navy"],
        ["secondary", "Secondary navy"],
        ["accent", "Accent colour"],
      ],
    },
    {
      title: "Backgrounds",
      description: "Passenger page, panels and structural borders.",
      fields: [
        ["background", "Page background"],
        ["panel", "Panel surface"],
        ["border", "Border colour"],
      ],
    },
    {
      title: "Message colours",
      description: "Passenger and assistant conversation bubbles.",
      fields: [
        ["user_message", "Passenger message"],
        ["assistant_message", "Assistant message"],
      ],
    },
    {
      title: "Text and controls",
      description: "Primary and supporting text used by the chatbot.",
      fields: [
        ["text", "Primary text"],
        ["secondary_text", "Secondary text"],
      ],
    },
  ];
  const defaults = {
    primary: "#102f70",
    secondary: "#102f70",
    accent: "#d71920",
    background: "#f7f9fc",
    panel: "#ffffff",
    user_message: "#d71920",
    assistant_message: "#eaf2fc",
    text: "#172033",
    secondary_text: "#64748b",
    border: "#dce4ef",
  };

  if (!theme || !formTheme) {
    return (
      <p className="admin-empty">Loading passenger appearance settings...</p>
    );
  }

  function updateColour(key, value) {
    setFormTheme((current) => ({
      ...current,
      [key]: value,
    }));
    setDirty(true);
  }

  async function save() {
    const saved = await savePassengerTheme(user, formTheme);
    setTheme(saved);
    setFormTheme(saved);
    setDirty(false);
    onSaved("Passenger theme saved and published.");
  }

  return (
    <div className="theme-settings">
      <div className="theme-settings-form">
        <div className="settings-intro">
          <h2>Passenger chatbot colours</h2>
          <p>
            These settings affect only passenger-facing screens. The
            administrator dashboard always keeps its own theme.
          </p>
        </div>
        {groups.map((group) => (
          <section className="theme-group" key={group.title}>
            <div className="theme-group-heading">
              <h3>{group.title}</h3>
              <p>{group.description}</p>
            </div>
            <div className="theme-field-grid">
              {group.fields.map(([key, label]) => (
                <label className="theme-field" key={key}>
                  {label}
                  <span>
                    <input
                      type="color"
                      value={formTheme[key]}
                      onChange={(event) => updateColour(
                        key,
                        event.target.value,
                      )}
                      aria-label={`${label} colour picker`}
                    />
                    <input
                      value={formTheme[key]}
                      pattern="#[0-9a-fA-F]{6}"
                      onChange={(event) => updateColour(
                        key,
                        event.target.value,
                      )}
                      aria-label={`${label} hexadecimal value`}
                    />
                  </span>
                </label>
              ))}
            </div>
          </section>
        ))}
        <div className="theme-actions">
          <button
            className="button admin-primary-button"
            type="button"
            onClick={save}
          >
            Save changes
          </button>
          <button
            className="button secondary"
            type="button"
            onClick={() => {
              if (
                window.confirm(
                  "Reset all passenger colours to the Kenya Airways defaults?",
                )
              )
                setFormTheme(defaults);
                setDirty(true);
            }}
          >
            Reset to default
          </button>
        </div>
      </div>
      <div className="theme-preview-wrap">
        <p className="eyebrow">LIVE PREVIEW</p>
        <div
          className="theme-preview"
          style={{
            "--preview-primary": formTheme.primary,
            "--preview-panel": formTheme.panel,
            "--preview-user": formTheme.user_message,
            "--preview-assistant": formTheme.assistant_message,
            "--preview-text": formTheme.text,
            "--preview-background": formTheme.background,
            "--preview-border": formTheme.border,
          }}
        >
          <header>
            <KenyaAirwaysLogo
              className="preview-airline-logo"
              variant="colour"
            />
            <strong>KQ Support</strong>
            <i>−</i>
          </header>
          <div className="theme-preview-messages">
            <p className="preview-user">What is my baggage allowance?</p>
            <div>
              <small>KQ Support</small>
              <p className="preview-assistant">
                Your baggage allowance depends on your route, cabin and fare.
              </p>
            </div>
          </div>
          <div className="preview-input">
            Enter your inquiry... <span>Send</span>
          </div>
        </div>
      </div>
    </div>
  );
}
export default ThemeSettings;
