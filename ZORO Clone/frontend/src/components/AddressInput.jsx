import { useState, useEffect, useRef } from "react";
import { fetchAddressSuggestions } from "../lib/usAddressValidator";
import { MapPinIcon } from "./Icons";

export default function AddressInput({
  id,
  value,
  onChange,
  onSelectSuggestion,
  placeholder = "Street address or P.O. Box",
  required = false,
  className = "z-input",
  disabled = false,
}) {
  const [suggestions, setSuggestions] = useState([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const wrapperRef = useRef(null);
  const debounceTimer = useRef(null);

  useEffect(() => {
    function handleClickOutside(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleInputChange = (e) => {
    const val = e.target.value;
    onChange(e);

    if (debounceTimer.current) clearTimeout(debounceTimer.current);

    if (val.trim().length >= 3) {
      setIsLoading(true);
      debounceTimer.current = setTimeout(async () => {
        const results = await fetchAddressSuggestions(val);
        setSuggestions(results);
        setIsOpen(results.length > 0);
        setIsLoading(false);
      }, 250);
    } else {
      setSuggestions([]);
      setIsOpen(false);
      setIsLoading(false);
    }
  };

  const handleSelect = (sug) => {
    setIsOpen(false);
    setSuggestions([]);
    if (onSelectSuggestion) {
      onSelectSuggestion(sug);
    }
  };

  return (
    <div ref={wrapperRef} className="relative w-full">
      <div className="relative flex items-center">
        <input
          id={id}
          type="text"
          className={className}
          placeholder={placeholder}
          value={value}
          onChange={handleInputChange}
          onFocus={() => {
            if (suggestions.length > 0) setIsOpen(true);
          }}
          autoComplete="address-line1"
          required={required}
          disabled={disabled}
        />
        {isLoading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <div className="w-3.5 h-3.5 border-2 border-slate-300 border-t-brand-teal rounded-full animate-spin" />
          </div>
        )}
      </div>

      {isOpen && suggestions.length > 0 && (
        <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-white rounded-lg shadow-xl border border-slate-200 overflow-hidden divide-y divide-slate-100 max-h-60 overflow-y-auto">
          <div className="px-3 py-1.5 text-[11px] font-bold text-slate-400 uppercase tracking-wider bg-slate-50 flex items-center justify-between">
            <span>Verified US Address Suggestions</span>
            <span className="text-[10px] text-slate-400 font-normal">OpenStreetMap Geocoder</span>
          </div>
          {suggestions.map((sug, idx) => (
            <button
              key={idx}
              type="button"
              className="w-full text-left px-3 py-2.5 hover:bg-emerald-50/60 focus:bg-emerald-50 transition-colors flex items-start gap-2.5 cursor-pointer group"
              onClick={() => handleSelect(sug)}
            >
              <div className="mt-0.5 text-slate-400 group-hover:text-emerald-700 flex-shrink-0">
                <MapPinIcon size={14} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-bold text-slate-800 group-hover:text-emerald-950 truncate">
                  {sug.address1}
                </div>
                <div className="text-[11px] text-slate-500 group-hover:text-emerald-800">
                  {sug.city ? `${sug.city}, ` : ""}{sug.state} {sug.zip}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
