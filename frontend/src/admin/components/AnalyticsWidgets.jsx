import { useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  Bot,
  ChartColumn,
  ChartPie,
  CircleAlert,
  CircleCheck,
  Clock3,
  Cpu,
  FileText,
  ListChecks,
  MessageCircleMore,
  MessageSquare,
  Plane,
  RefreshCcw,
  Star,
  UserPlus,
  UsersRound,
  X,
} from "lucide-react";
import {
  activityPresentation,
  formatChartLabel,
  formatChartTooltip,
  formatDate,
  formatDateTime,
  labelLanguages,
  languageLabel,
  modelDisplayName,
  readableEventName,
  readableMetricName,
} from "../utils";

function MetricCard({ label, value, hint, icon: Icon, accent = "navy" }) {
  return (
    <article className={`admin-metric-card accent-${accent}`}>
      <div className="admin-metric-heading">
        <span aria-hidden="true">
          {Icon && <Icon size={21} strokeWidth={1.9} />}
        </span>
        <p>{label}</p>
      </div>
      <strong>{value}</strong>
      <small>{hint}</small>
    </article>
  );
}

function Overview({
  analytics,
  onNavigate,
  onOpenConversation,
  onOpenLog,
}) {
  if (!analytics)
    return (
      <p className="admin-empty">
        No overview data is available for this period.
      </p>
    );
  return (
    <div className="admin-overview">
      <div className="admin-metric-grid">
        <MetricCard
          label="Registered users"
          value={analytics.totals.users}
          hint="Current Firebase accounts"
          icon={UsersRound}
          accent="red"
        />
        <MetricCard
          label="Conversations"
          value={analytics.totals.conversations}
          hint={`Started in the last ${analytics.period_days} days`}
          icon={MessageSquare}
          accent="navy"
        />
        <MetricCard
          label="Active documents"
          value={analytics.totals.active_documents}
          hint={`${analytics.totals.documents} document records total`}
          icon={FileText}
          accent="amber"
        />
        <MetricCard
          label="Average satisfaction"
          value={
            analytics.feedback.count
              ? `${analytics.feedback.average_rating}/5`
              : "No ratings"
          }
          hint={`${analytics.feedback.count} ratings in period`}
          icon={Star}
          accent="green"
        />
        <MetricCard
          label="OAG API Calls"
          value={analytics.totals.oag_api_calls}
          hint={
            `Flight information requests in the last `
            + `${analytics.period_days} days`
          }
          icon={Plane}
          accent="blue"
        />
      </div>
      <AnalyticsPanels analytics={analytics} compact />
      <div className="admin-overview-lower">
        <RecentConversations
          items={analytics.recent_conversations}
          onViewAll={() => onNavigate("Conversations")}
          onOpen={onOpenConversation}
        />
        <RecentActivity
          items={analytics.recent_activity}
          onViewAll={() => onNavigate("Logs")}
          onOpen={onOpenLog}
        />
      </div>
    </div>
  );
}

function Analytics({ analytics }) {
  if (!analytics)
    return (
      <p className="admin-empty">
        No analytics data is available for this period.
      </p>
    );
  return (
    <div className="analytics-page">
      <div className="admin-metric-grid analytics-kpis">
        <MetricCard
          label="Conversations"
          value={analytics.totals.conversations}
          hint={`Started in the last ${analytics.period_days} days`}
          icon={MessageSquare}
          accent="navy"
        />
        <MetricCard
          label="Completed responses"
          value={analytics.performance.completed_count}
          hint="Successfully completed generations"
          icon={CircleCheck}
          accent="green"
        />
        <MetricCard
          label="Average response"
          value={
            analytics.performance.completed_count
              ? `${(analytics.performance.average_response_ms / 1000).toFixed(2)} s`
              : "Unavailable"
          }
          hint="Across completed responses"
          icon={Clock3}
          accent="amber"
        />
        <MetricCard
          label="Satisfaction"
          value={
            analytics.feedback.count
              ? `${analytics.feedback.average_rating}/5 ★`
              : "No ratings"
          }
          hint={`${analytics.feedback.count} submitted ratings`}
          icon={Star}
          accent="purple"
        />
        <MetricCard
          label="OAG API Calls"
          value={analytics.totals.oag_api_calls}
          hint={
            `Flight information API requests in the last `
            + `${analytics.period_days} days`
          }
          icon={Plane}
          accent="blue"
        />
      </div>
      <AnalyticsPanels analytics={analytics} />
    </div>
  );
}

function AnalyticsPanels({ analytics, compact = false }) {
  const activitySeries = buildActivitySeries(
    analytics.conversation_activity,
    analytics.period_days,
  );
  // Seven and thirty-day reports retain daily data. The ninety-day view uses
  // weekly sums so totals are preserved without squeezing ninety bars.
  const labelStride =
    analytics.period_days <= 7 ? 1 : analytics.period_days <= 30 ? 5 : 1;
  return (
    <div className={`admin-analytics-grid ${compact ? "compact" : ""}`}>
      <section className="admin-analytics-card activity-card">
        <h2>
          <ChartColumn size={21} /> Conversation activity
        </h2>
        <p className="admin-metric-note">
          {analytics.period_days === 90
            ? "Weekly conversation totals for the last 90 days."
            : `Daily conversations started during the last ${analytics.period_days} days.`}{" "}
          Hover or focus a bar for exact details.
        </p>
        <ConversationActivityChart
          items={activitySeries}
          labelStride={labelStride}
        />
      </section>
      <section className="admin-analytics-card distribution-card">
        <h2>
          <ChartPie size={21} /> Language distribution
        </h2>
        <LanguagePie values={analytics.languages} />
        <h2>
          <Cpu size={20} /> AI model usage
        </h2>
        <MetricList
          values={analytics.models}
          empty="No model usage data."
          progress
        />
      </section>
      {!compact && (
        <>
          <section className="admin-analytics-card quality-card">
            <h2>
              <CircleCheck size={20} /> Response quality and timing
            </h2>
            <div className="quality-metric-grid">
              <div>
                <CircleCheck size={18} />
                <span>
                  Completed responses
                  <strong>{analytics.performance.completed_count}</strong>
                </span>
              </div>
              <div>
                <Clock3 size={18} />
                <span>
                  Average response
                  <strong>
                    {analytics.performance.completed_count
                      ? `${(analytics.performance.average_response_ms / 1000).toFixed(2)} seconds`
                      : "Unavailable"}
                  </strong>
                </span>
              </div>
              <div>
                <Star size={18} />
                <span>
                  Feedback rating
                  <strong>
                    {analytics.feedback.count
                      ? `${analytics.feedback.average_rating}/5`
                      : "No ratings"}
                  </strong>
                </span>
              </div>
            </div>
          </section>
          <section className="admin-analytics-card outcomes-card">
            <h2>Fallbacks and failures</h2>
            <MetricList
              values={analytics.responses}
              empty="No response outcomes recorded."
            />
            <MetricList
              values={analytics.failures}
              empty="No failures recorded."
            />
          </section>
        </>
      )}
      {!compact && (
        <section className="admin-analytics-card satisfaction-card">
          <h2>
            <Star size={20} /> User satisfaction
          </h2>
          <div className="satisfaction-summary">
            <strong>
              {analytics.feedback.count
                ? `${analytics.feedback.average_rating}/5`
                : "No ratings"}
            </strong>
            <span>{analytics.feedback.count} submitted ratings</span>
          </div>
          <RatingDistribution values={analytics.rating_distribution} />
        </section>
      )}
      {!compact && (
        <section className="admin-data-notice">
          <CircleAlert size={19} />
          <div>
            <strong>Data availability</strong>
            <p>
              Query categories, RAG evaluation scores, and CSV export are not
              recorded by the current backend, so they are intentionally not
              estimated here.
            </p>
          </div>
        </section>
      )}
    </div>
  );
}

function ConversationActivityChart({ items, labelStride }) {
  const [activeItem, setActiveItem] = useState(null);
  const chartWrapRef = useRef(null);

  if (!items.length) {
    return <p className="admin-empty">No conversations in this period.</p>;
  }

  const width = 960;
  const height = 640;
  const plot = { left: 58, right: 16, top: 14, bottom: 52 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const highestCount = Math.max(1, ...items.map((item) => item.count));
  const tickCount = Math.min(4, highestCount);
  const tickStep = Math.max(1, Math.ceil(highestCount / tickCount));
  const axisMaximum = tickStep * tickCount;
  const ticks = Array.from(
    { length: tickCount + 1 },
    (_, index) => axisMaximum - index * tickStep,
  );
  const slotWidth = plotWidth / items.length;
  const barWidth = Math.max(4, Math.min(54, slotWidth * 0.72));

  // Bars, grid lines, and labels use this one coordinate system so a value can
  // never visually disagree with the y-axis scale.
  const yForValue = (value) =>
    plot.top + plotHeight - (value / axisMaximum) * plotHeight;

  const showTooltip = (item, event) => {
    const wrapperBounds = chartWrapRef.current?.getBoundingClientRect();
    const targetBounds = event.currentTarget.getBoundingClientRect();

    if (!wrapperBounds) return;

    // Use rendered browser coordinates rather than raw SVG coordinates. This
    // accounts for responsive SVG letterboxing and keeps the tooltip beside
    // the focused bar at every card width.
    setActiveItem({
      ...item,
      left: Math.min(
        wrapperBounds.width - 96,
        Math.max(
          96,
          targetBounds.left - wrapperBounds.left + targetBounds.width / 2,
        ),
      ),
      top: Math.max(72, targetBounds.top - wrapperBounds.top),
    });
  };

  return (
    <div className="activity-chart-svg-wrap" ref={chartWrapRef}>
      <svg
        className="activity-chart-svg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Conversation activity bar chart"
        preserveAspectRatio="xMidYMax meet"
      >
        {ticks.map((tick) => {
          const y = yForValue(tick);
          return (
            <g key={tick}>
              <line
                className="activity-grid-line"
                x1={plot.left}
                x2={width - plot.right}
                y1={y}
                y2={y}
              />
              <text
                className="activity-axis-label"
                x={plot.left - 9}
                y={y + 4}
                textAnchor="end"
              >
                {tick}
              </text>
            </g>
          );
        })}
        {items.map((item, index) => {
          const x = plot.left + index * slotWidth + (slotWidth - barWidth) / 2;
          const y = yForValue(item.count);
          const barHeight = Math.max(0, plot.top + plotHeight - y);
          const showLabel =
            index % labelStride === 0 || index === items.length - 1;
          return (
            <g
              className="activity-svg-bar"
              key={item.key}
              tabIndex="0"
              aria-label={`${item.tooltip}: ${item.count} conversation${item.count === 1 ? "" : "s"}`}
              onMouseEnter={(event) => showTooltip(item, event)}
              onMouseLeave={() => setActiveItem(null)}
              onFocus={(event) => showTooltip(item, event)}
              onBlur={() => setActiveItem(null)}
            >
              <rect x={x} y={y} width={barWidth} height={barHeight} rx="5" />
              {/* A transparent target keeps zero-count dates interactive. */}
              {item.count === 0 && (
                <rect
                  className="activity-zero-target"
                  x={x}
                  y={plot.top + plotHeight - 12}
                  width={barWidth}
                  height="12"
                  rx="4"
                />
              )}
              {showLabel && (
                <text
                  className="activity-x-label"
                  x={x + barWidth / 2}
                  y={height - 15}
                  textAnchor="middle"
                >
                  {item.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {activeItem && (
        <div
          className="activity-chart-tooltip"
          role="status"
          style={{ left: `${activeItem.left}px`, top: `${activeItem.top}px` }}
        >
          <span>{activeItem.endDate ? "Week" : "Date"}</span>
          <strong>{activeItem.tooltip}</strong>
          <span>Conversations</span>
          <strong>{activeItem.count}</strong>
        </div>
      )}
    </div>
  );
}

function buildActivitySeries(items, periodDays) {
  if (periodDays !== 90) {
    return items.map((item) => ({
      ...item,
      key: item.date,
      label: formatChartLabel(item.date),
      tooltip: formatChartTooltip(item.date),
      startDate: item.date,
    }));
  }

  const weeks = [];

  for (let index = 0; index < items.length; index += 7) {
    const days = items.slice(index, index + 7);
    const first = days[0];
    const last = days[days.length - 1];

    if (!first || !last) {
      continue;
    }

    weeks.push({
      key: `${first.date}-${last.date}`,
      label: formatChartLabel(first.date),
      tooltip: `${formatChartTooltip(first.date)} – ${formatChartTooltip(last.date)}`,
      startDate: first.date,
      endDate: last.date,
      count: days.reduce((sum, day) => sum + day.count, 0),
    });
  }

  return weeks;
}

function RecentConversations({ items, onViewAll, onOpen }) {
  const recentItems = [...items]
    .sort(
      (left, right) => new Date(right.started_at) - new Date(left.started_at),
    )
    .slice(0, 6);
  return (
    <section className="admin-analytics-card recent-card">
      <div className="admin-card-heading">
        <h2>
          <MessageCircleMore size={20} /> Recent conversations
        </h2>
        <button className="text-button" type="button" onClick={onViewAll}>
          View all <ArrowRight size={15} />
        </button>
      </div>
      {recentItems.length ? (
        <div className="admin-recent-list conversation-list">
          {recentItems.map((item) => (
            <button
              className="recent-conversation-row overview-recent-button"
              key={item.conversation_id}
              type="button"
              onClick={() => onOpen(item.conversation_id)}
              aria-label={`Inspect conversation: ${item.title}`}
            >
              <span
                className="recent-indicator conversation"
                aria-hidden="true"
              >
                <MessageSquare size={17} />
              </span>
              <div className="recent-row-copy">
                <span className="recent-title-line">
                  <strong title={item.title || item.first_message}>
                    {item.title
                      || item.first_message
                      || "Untitled conversation"}
                  </strong>
                  <span className={`overview-status-badge ${item.status}`}>
                    {readableEventName(item.status)}
                  </span>
                </span>
                <small>
                  {languageLabel(item.language)} · {item.message_count} messages
                  {item.model_id
                    ? ` · ${modelDisplayName(item.model_id)}`
                    : ""}
                </small>
              </div>
              <time dateTime={item.started_at}>
                {formatDateTime(item.started_at)}
              </time>
            </button>
          ))}
        </div>
      ) : (
        <p className="admin-metric-note">No conversations in this period.</p>
      )}
    </section>
  );
}

function RecentActivity({ items, onViewAll, onOpen }) {
  const recentItems = [...items]
    .sort((left, right) => new Date(right.timestamp) - new Date(left.timestamp))
    .slice(0, 6);
  return (
    <section className="admin-analytics-card recent-card">
      <div className="admin-card-heading">
        <h2>
          <Activity size={20} /> Recent system activity
        </h2>
        <button className="text-button" type="button" onClick={onViewAll}>
          View all <ArrowRight size={15} />
        </button>
      </div>
      {recentItems.length ? (
        <div className="admin-recent-list activity-list">
          {recentItems.map((item, index) => {
            const presentation = activityPresentation(item.action);
            const ActivityIcon = presentation.icon;
            return (
              <button
                className={`recent-activity-row overview-recent-button severity-${
                  item.severity || "info"
                }`}
                key={`${item.timestamp}-${index}`}
                type="button"
                onClick={() => onOpen(item.event_id)}
                aria-label={`Open log details: ${readableEventName(item.action)}`}
              >
                <span
                  className={`recent-indicator ${presentation.tone}`}
                  aria-hidden="true"
                >
                  <ActivityIcon size={17} />
                </span>
                <div className="recent-row-copy">
                  <strong>{readableEventName(item.action)}</strong>
                  <small title={item.summary}>
                    {item.summary || readableEventName(item.target_type)}
                    {item.actor ? ` · ${item.actor}` : ""}
                  </small>
                </div>
                <time dateTime={item.timestamp}>
                  {formatDateTime(item.timestamp)}
                </time>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="admin-metric-note">
          No administrator activity recorded in this period.
        </p>
      )}
    </section>
  );
}

function MetricList({ values, empty, progress = false }) {
  const entries = Object.entries(values || {});
  const maximum = Math.max(1, ...entries.map(([, count]) => count));
  return entries.length ? (
    <ul className={`admin-metric-list ${progress ? "with-progress" : ""}`}>
      {entries.map(([name, count]) => (
        <li key={name}>
          <span>
            {progress ? modelDisplayName(name) : readableMetricName(name)}
          </span>
          <strong>{count}</strong>
          {progress && (
            <i aria-hidden="true">
              <span style={{ width: `${(count / maximum) * 100}%` }} />
            </i>
          )}
        </li>
      ))}
    </ul>
  ) : (
    <p className="admin-metric-note">{empty}</p>
  );
}

function LanguagePie({ values }) {
  const labels = Object.entries(values || {}).map(([key, value]) => [
    languageLabel(key),
    value,
  ]);
  const total = labels.reduce((sum, [, value]) => sum + value, 0);
  if (!total) return <p className="admin-metric-note">No language data.</p>;
  // Complementary colours distinguish languages without changing the counts
  // or implying that one language has a higher quality ranking.
  const colors = ["#0b2347", "#d71920", "#a9b8cc", "#6f86a6"];
  let start = 0;
  const stops = labels.map(([label, value], index) => {
    const end = start + (value / total) * 360;
    const stop = `${colors[index % colors.length]} ${start}deg ${end}deg`;
    start = end;
    return { label, value, stop, color: colors[index % colors.length] };
  });
  return (
    <div className="language-pie-layout">
      <div
        className="language-pie"
        style={{
          background: `conic-gradient(${stops.map((item) => item.stop).join(", ")})`,
        }}
        aria-label={`Language distribution chart, ${total} total conversations`}
      >
        <span>
          <strong>{total}</strong>
          <small>Total</small>
        </span>
      </div>
      <ul className="language-pie-key">
        {stops.map((item) => (
          <li key={item.label}>
            <span style={{ background: item.color }} />
            {item.label}
            <strong>
              {item.value} ({Math.round((item.value / total) * 100)}%)
            </strong>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RatingDistribution({ values }) {
  const ratings = [5, 4, 3, 2, 1];
  if (!Object.keys(values || {}).length)
    return (
      <p className="admin-metric-note">No rating distribution is available.</p>
    );
  return (
    <div className="rating-distribution">
      {ratings.map((rating) => (
        <div className="rating-row" key={rating}>
          <span
            className="rating-stars"
            aria-label={`${rating} out of 5 stars`}
          >
            {"★".repeat(rating)}
            <span className="rating-stars-muted">{"★".repeat(5 - rating)}</span>
          </span>
          <span className="rating-label">{rating}/5</span>
          <strong>{values[String(rating)] || 0}</strong>
        </div>
      ))}
    </div>
  );
}

export { Overview, Analytics, ConversationActivityChart, MetricCard };
