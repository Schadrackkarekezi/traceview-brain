// Small inline-SVG icon set - no icon library pulled in for a handful of
// simple line icons; keeps the bundle light and every icon consistent
// (1.6px stroke, 18px box) since they're all hand-drawn to the same spec.

const base = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

export function HomeIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 11.5 12 4l8 7.5" />
      <path d="M6 10v9a1 1 0 0 0 1 1h3v-6h4v6h3a1 1 0 0 0 1-1v-9" />
    </svg>
  );
}

export function ChatIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 4v-4H5.5A1.5 1.5 0 0 1 4 14.5z" />
      <circle cx="9" cy="10" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="12" cy="10" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="15" cy="10" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function TerminalIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
      <path d="M7 9.5 10.5 12 7 14.5" />
      <path d="M12.5 14.5h4.5" />
    </svg>
  );
}

export function GraphIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="6" cy="7" r="2.3" />
      <circle cx="18" cy="7" r="2.3" />
      <circle cx="12" cy="18" r="2.3" />
      <path d="M8 8.3 10.3 16" />
      <path d="M16 8.3 13.7 16" />
      <path d="M8.3 7h7.4" />
    </svg>
  );
}

export function ChartIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 20V10" />
      <path d="M12 20V4" />
      <path d="M20 20v-7" />
      <path d="M3 20h18" />
    </svg>
  );
}

export function SunIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v2M12 19v2M5 5l1.4 1.4M17.6 17.6 19 19M3 12h2M19 12h2M5 19l1.4-1.4M17.6 6.4 19 5" />
    </svg>
  );
}

export function MoonIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" />
    </svg>
  );
}

export function AutoIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 3.5A8.5 8.5 0 0 1 12 20.5Z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function SparkleIcon(props) {
  return (
    <svg {...base} {...props} fill="currentColor" stroke="none">
      <path d="M12 3.5c.3 2.7 1 4.3 2.1 5.4 1.1 1.1 2.7 1.8 5.4 2.1-2.7.3-4.3 1-5.4 2.1-1.1 1.1-1.8 2.7-2.1 5.4-.3-2.7-1-4.3-2.1-5.4C8.8 12 7.2 11.3 4.5 11c2.7-.3 4.3-1 5.4-2.1C11 7.8 11.7 6.2 12 3.5Z" />
      <path d="M19 3.2c.13 1 .38 1.63.8 2.05.42.42 1.05.67 2.05.8-1 .13-1.63.38-2.05.8-.42.42-.67 1.05-.8 2.05a3.6 3.6 0 0 0-.8-2.05A3.6 3.6 0 0 0 16.15 6c1-.13 1.63-.38 2.05-.8.42-.42.67-1.05.8-2Z" />
    </svg>
  );
}

export function ChevronDownIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function UploadIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 16V4" />
      <path d="m7 8.5 5-5 5 5" />
      <path d="M4.5 15v3.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V15" />
    </svg>
  );
}

export function DatabaseIcon(props) {
  return (
    <svg {...base} {...props}>
      <ellipse cx="12" cy="6" rx="7.5" ry="2.6" />
      <path d="M4.5 6v6c0 1.4 3.4 2.6 7.5 2.6s7.5-1.2 7.5-2.6V6" />
      <path d="M4.5 12v6c0 1.4 3.4 2.6 7.5 2.6s7.5-1.2 7.5-2.6v-6" />
    </svg>
  );
}

export function SearchIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.3-4.3" />
    </svg>
  );
}

export function SendIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M20 4 10.5 13.5" />
      <path d="M20 4 13.5 20l-3-6.5L4 10.5z" />
    </svg>
  );
}

export function ClockIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3.2 2" />
    </svg>
  );
}

export function CoinIcon(props) {
  return (
    <svg {...base} {...props}>
      <ellipse cx="12" cy="8" rx="7.5" ry="3.2" />
      <path d="M4.5 8v8c0 1.77 3.36 3.2 7.5 3.2s7.5-1.43 7.5-3.2V8" />
      <path d="M12 6v8" />
      <path d="M9.8 8c0-1 1-1.6 2.2-1.6s2.2.6 2.2 1.4c0 2-4.4 1-4.4 2.9 0 .9 1 1.5 2.2 1.5s2.2-.5 2.2-1.4" />
    </svg>
  );
}

export function CheckCircleIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m8.3 12.3 2.5 2.5 5-5.2" />
    </svg>
  );
}

export function TargetIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.7" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function PlayIcon(props) {
  return (
    <svg {...base} {...props} fill="currentColor" stroke="none">
      <path d="M7 4.8v14.4c0 .7.77 1.13 1.37.76l11.4-7.2a.9.9 0 0 0 0-1.52L8.37 4.04A.9.9 0 0 0 7 4.8Z" />
    </svg>
  );
}

export function PlusIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  );
}

export function CloseIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="m6 6 12 12" />
      <path d="m18 6-12 12" />
    </svg>
  );
}

export function MenuIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 6h16" />
      <path d="M4 12h16" />
      <path d="M4 18h16" />
    </svg>
  );
}

export function ArrowRightIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 12h16" />
      <path d="m13 6 6 6-6 6" />
    </svg>
  );
}

export function BrainIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M9.5 4.5a2.5 2.5 0 0 0-2.4 3.2A3 3 0 0 0 5 10.5c0 .9.4 1.7 1 2.3a3 3 0 0 0 1.6 4.6A2.5 2.5 0 0 0 12 18.5v-12a2 2 0 0 0-2.5-2Z" />
      <path d="M14.5 4.5a2.5 2.5 0 0 1 2.4 3.2 3 3 0 0 1 2.1 2.8c0 .9-.4 1.7-1 2.3a3 3 0 0 1-1.6 4.6 2.5 2.5 0 0 1-4.4 1.1v-12a2 2 0 0 1 2.5-2Z" />
    </svg>
  );
}
