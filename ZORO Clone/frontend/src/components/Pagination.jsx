function Pagination({ page, pages, hasNext, hasPrev, onPageChange }) {
  if (pages <= 1) return null;

  // Build page numbers to display (show max 7 pages around current)
  const pageNumbers = [];
  const maxVisible = 7;
  let start = Math.max(1, page - Math.floor(maxVisible / 2));
  let end = Math.min(pages, start + maxVisible - 1);
  if (end - start < maxVisible - 1) {
    start = Math.max(1, end - maxVisible + 1);
  }

  for (let i = start; i <= end; i++) {
    pageNumbers.push(i);
  }

  return (
    <div className="pagination" id="pagination">
      <button
        className="pagination__btn"
        onClick={() => onPageChange(page - 1)}
        disabled={!hasPrev}
        aria-label="Previous page"
      >
        ← Prev
      </button>

      <div className="pagination__pages">
        {start > 1 && (
          <>
            <button
              className="pagination__page"
              onClick={() => onPageChange(1)}
            >
              1
            </button>
            {start > 2 && <span className="pagination__dots">…</span>}
          </>
        )}

        {pageNumbers.map((num) => (
          <button
            key={num}
            className={`pagination__page ${num === page ? "pagination__page--active" : ""}`}
            onClick={() => onPageChange(num)}
          >
            {num}
          </button>
        ))}

        {end < pages && (
          <>
            {end < pages - 1 && <span className="pagination__dots">…</span>}
            <button
              className="pagination__page"
              onClick={() => onPageChange(pages)}
            >
              {pages}
            </button>
          </>
        )}
      </div>

      <button
        className="pagination__btn"
        onClick={() => onPageChange(page + 1)}
        disabled={!hasNext}
        aria-label="Next page"
      >
        Next →
      </button>
    </div>
  );
}

export default Pagination;
