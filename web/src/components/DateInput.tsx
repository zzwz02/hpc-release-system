import { useRef } from "react";
import { formatDateValue } from "../lib/time";

interface DateInputProps {
  value: string;
  onChange: (value: string) => void;
  testId: string;
  ariaLabel: string;
  size?: "default" | "sm";
}

export function DateInput({
  value,
  onChange,
  testId,
  ariaLabel,
  size = "default",
}: DateInputProps) {
  const pickerRef = useRef<HTMLInputElement>(null);
  const dateValue = formatDateValue(value);
  const sizeClass = size === "sm" ? " sm" : "";

  function openPicker() {
    const picker = pickerRef.current as (HTMLInputElement & { showPicker?: () => void }) | null;
    if (!picker) return;
    try {
      if (typeof picker.showPicker === "function") {
        picker.showPicker();
      } else {
        picker.click();
      }
    } catch {
      picker.click();
    }
  }

  return (
    <div className={`date-input-wrap${sizeClass}`}>
      <input
        className={`input${sizeClass} date-input-text`}
        type="text"
        inputMode="numeric"
        pattern="\d{4}-\d{2}-\d{2}"
        placeholder="YYYY-MM-DD"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => onChange(formatDateValue(value))}
        data-testid={testId}
        aria-label={ariaLabel}
      />
      <button
        type="button"
        className="date-picker-btn"
        onClick={openPicker}
        aria-label={`${ariaLabel} 选择日期`}
        data-testid={`${testId}-calendar`}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M7 2v3M17 2v3M4 9h16" />
          <rect x="4" y="5" width="16" height="16" rx="2" />
          <path d="M8 13h.01M12 13h.01M16 13h.01M8 17h.01M12 17h.01" />
        </svg>
      </button>
      <input
        ref={pickerRef}
        className="date-picker-native"
        type="date"
        value={dateValue}
        onChange={(e) => onChange(e.target.value)}
        tabIndex={-1}
        aria-hidden="true"
        data-testid={`${testId}-native`}
      />
    </div>
  );
}
