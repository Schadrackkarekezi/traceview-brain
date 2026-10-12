import { useTheme } from "../useTheme";
import { SunIcon, MoonIcon, AutoIcon } from "./Icons";

const OPTIONS = [
  { id: "light", label: "Light", Icon: SunIcon },
  { id: "dark", label: "Dark", Icon: MoonIcon },
  { id: "system", label: "Auto", Icon: AutoIcon },
];

export default function ThemeToggle() {
  const [theme, setTheme] = useTheme();

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Theme">
      {OPTIONS.map(({ id, label, Icon }) => (
        <button
          key={id}
          className={`theme-toggle-btn${theme === id ? " active" : ""}`}
          onClick={() => setTheme(id)}
          title={label}
          aria-checked={theme === id}
          role="radio"
        >
          <Icon width={14} height={14} />
        </button>
      ))}
    </div>
  );
}
