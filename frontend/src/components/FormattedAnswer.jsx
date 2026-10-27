const MARKDOWN_LINK = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
const BOLD = /\*\*([^*]+)\*\*/g;
const BARE_URL = /(https?:\/\/[^\s<]+)/g;

function safeHttpUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return url.href;
    }
  } catch {
    return null;
  }

  return null;
}

function trimTrailingUrlPunctuation(value) {
  return value.replace(/[),.;:!?]+$/g, "");
}

function linkLabel(href) {
  try {
    const url = new URL(href);
    return url.hostname.replace(/^www\./, "") + (url.pathname === "/" ? "" : url.pathname);
  } catch {
    return href;
  }
}

function renderInline(text, keyPrefix) {
  const pieces = [];
  const linkPattern = new RegExp(`${MARKDOWN_LINK.source}|${BOLD.source}|${BARE_URL.source}`, "g");
  let cursor = 0;
  let match = linkPattern.exec(text);
  let part = 0;

  while (match) {
    if (match.index > cursor) {
      pieces.push(text.slice(cursor, match.index));
    }

    if (match[1] && match[2]) {
      const href = safeHttpUrl(match[2]);
      pieces.push(
        href ? (
          <a key={`${keyPrefix}-md-${part}`} href={href} target="_blank" rel="noreferrer noopener">
            {match[1]}
          </a>
        ) : match[1],
      );
    } else if (match[3]) {
      pieces.push(<strong key={`${keyPrefix}-b-${part}`}>{match[3]}</strong>);
    } else if (match[4]) {
      const href = safeHttpUrl(trimTrailingUrlPunctuation(match[4]));
      const trailing = href ? match[4].slice(href.length) : "";
      pieces.push(
        href ? (
          <a key={`${keyPrefix}-url-${part}`} href={href} target="_blank" rel="noreferrer noopener">
            {linkLabel(href)}
          </a>
        ) : match[4],
      );
      if (trailing) {
        pieces.push(trailing);
      }
    }

    cursor = match.index + match[0].length;
    part += 1;
    match = linkPattern.exec(text);
  }

  if (cursor < text.length) {
    pieces.push(text.slice(cursor));
  }

  return pieces;
}

function parseBlocks(text) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let list = null;

  function closeList() {
    if (list) {
      blocks.push(list);
      list = null;
    }
  }

  for (const line of lines) {
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const heading = line.match(/^\s{0,3}#{1,3}\s+(.+)$/);

    if (bullet) {
      if (!list) {
        list = { type: "list", items: [] };
      }
      list.items.push(bullet[1]);
      continue;
    }

    closeList();

    if (!line.trim()) {
      continue;
    }

    if (heading) {
      blocks.push({ type: "heading", text: heading[1] });
      continue;
    }

    const boldHeading = line.trim().match(/^\*\*(.+?)\*\*:?\s*$/);
    if (boldHeading) {
      blocks.push({ type: "heading", text: boldHeading[1] });
      continue;
    }

    if (/^sources:/i.test(line.trim())) {
      continue;
    }

    blocks.push({ type: "paragraph", text: line });
  }

  closeList();
  return blocks;
}

export function FormattedAnswer({ text }) {
  const blocks = parseBlocks(text);

  return (
    <div className="formatted-answer">
      {blocks.map((block, index) => {
        if (block.type === "list") {
          return (
            <ul key={`list-${index}`}>
              {block.items.map((item, itemIndex) => (
                <li key={`item-${index}-${itemIndex}`}>{renderInline(item, `${index}-${itemIndex}`)}</li>
              ))}
            </ul>
          );
        }

        if (block.type === "heading") {
          return <h3 key={`heading-${index}`}>{renderInline(block.text, `h-${index}`)}</h3>;
        }

        return <p key={`p-${index}`}>{renderInline(block.text, `p-${index}`)}</p>;
      })}
    </div>
  );
}
