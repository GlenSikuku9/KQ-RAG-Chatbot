const MARKDOWN_LINK = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
const BOLD = /\*\*([^*]+)\*\*/g;
const BARE_URL = /(https?:\/\/[^\s<]+)/g;

function normalizeAnswer(text) {
  return text
    .replace(/[\u200B\u200C\u200D\uFEFF]/g, "")
    .replace(/[\u00A0\u202F]/g, " ")
    .replace(/\\\|/g, "|")
    // Some providers return valid Markdown blocks on one line. Restore only
    // unambiguous block boundaries so headings, lists and tables remain safe.
    .replace(/\s+---\s+/g, "\n\n")
    .replace(/\s+(?=#{1,3}\s)/g, "\n\n")
    .replace(/\|\s+\|(?=[^|\n]+\|)/g, "|\n|")
    .replace(/\|\s+(?=\*\*[^|\n]+(?::\*\*|\*\*:))/g, "|\n\n")
    .replace(/\s+(?=(?:[-*]|\d+\.)\s+\*\*)/g, "\n")
    .replace(/(^\s*\|.*\|\s*$)\n\s*\n(?=\s*\|)/gm, "$1\n");
}

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
  const lines = normalizeAnswer(text).replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let list = null;

  function tableCells(line) {
    return line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());
  }

  function isTableDivider(line) {
    const cells = tableCells(line);
    return cells.length > 1 && cells.every(
      (cell) => /^:?-{3,}:?$/.test(cell),
    );
  }

  function closeList() {
    if (list) {
      blocks.push(list);
      list = null;
    }
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const heading = line.match(/^\s{0,3}#{1,3}\s+(.+)$/);

    if (
      line.trim().startsWith("|")
      && index + 1 < lines.length
      && isTableDivider(lines[index + 1])
    ) {
      closeList();
      const headers = tableCells(line);
      const rows = [];
      index += 2;

      while (
        index < lines.length
        && lines[index].trim().startsWith("|")
      ) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }

      blocks.push({ type: "table", headers, rows });
      index -= 1;
      continue;
    }

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
        if (block.type === "table") {
          return (
            <div
              className="formatted-answer-table-wrap"
              key={`table-${index}`}
              role="region"
              aria-label="Response table"
              tabIndex="0"
            >
              <table>
                <thead>
                  <tr>
                    {block.headers.map((header, cellIndex) => (
                      <th key={`head-${index}-${cellIndex}`} scope="col">
                        {renderInline(header, `th-${index}-${cellIndex}`)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={`row-${index}-${rowIndex}`}>
                      {block.headers.map((_, cellIndex) => (
                        <td key={`cell-${index}-${rowIndex}-${cellIndex}`}>
                          {renderInline(
                            row[cellIndex] || "",
                            `td-${index}-${rowIndex}-${cellIndex}`,
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

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
