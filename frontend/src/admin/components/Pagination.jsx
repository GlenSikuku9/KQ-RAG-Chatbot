function TablePagination({ page, pageSize, total, noun, onPage, onPageSize }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const start = total ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(page * pageSize, total);
  const firstPage = Math.max(1, Math.min(page - 2, pageCount - 4));
  const visiblePages = Array.from(
    { length: Math.min(5, pageCount) },
    (_, index) => firstPage + index,
  );
  return (
    <div className="admin-pagination table-pagination">
      <p>
        Showing {start}–{end} of {total} {noun}
      </p>
      <label>
        Rows per page
        <select
          value={pageSize}
          onChange={(event) => onPageSize(Number(event.target.value))}
        >
          <option value="10">10</option>
          <option value="20">20</option>
          <option value="50">50</option>
        </select>
      </label>
      <div>
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </button>
        {visiblePages.map((number) => (
          <button
            className={number === page ? "selected" : ""}
            type="button"
            key={number}
            onClick={() => onPage(number)}
            aria-current={number === page ? "page" : undefined}
          >
            {number}
          </button>
        ))}
        <button
          type="button"
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
}

function TableLoadingRows({ columns }) {
  return (
    <tr className="table-loading-row">
      <td colSpan={columns}>
        <span />
        <span />
        <span />
      </td>
    </tr>
  );
}
export { TablePagination as Pagination, TableLoadingRows };
