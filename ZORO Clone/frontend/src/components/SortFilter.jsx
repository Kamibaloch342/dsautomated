function SortFilter({ sort, onSortChange, inStock, onInStockChange }) {
  return (
    <div className="sort-filter" id="sort-filter">
      <div className="sort-filter__group">
        <label htmlFor="sort-select" className="sort-filter__label">
          Sort by:
        </label>
        <select
          id="sort-select"
          className="sort-filter__select"
          value={sort}
          onChange={(e) => onSortChange(e.target.value)}
        >
          <option value="">Default</option>
          <option value="price_asc">Price: Low to High</option>
          <option value="price_desc">Price: High to Low</option>
          <option value="name_asc">Name: A → Z</option>
          <option value="name_desc">Name: Z → A</option>
          <option value="newest">Newest</option>
        </select>
      </div>

      <label className="sort-filter__toggle" htmlFor="instock-toggle">
        <input
          id="instock-toggle"
          type="checkbox"
          checked={inStock}
          onChange={(e) => onInStockChange(e.target.checked)}
        />
        <span className="sort-filter__toggle-label">In Stock Only</span>
      </label>
    </div>
  );
}

export default SortFilter;
