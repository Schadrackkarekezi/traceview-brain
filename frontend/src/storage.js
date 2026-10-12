// Safe read-with-fallback for localStorage/sessionStorage - both throw in
// some environments (private browsing, storage disabled) rather than just
// returning null, so every call site needs the same try/catch either way.

export function readJSON(storage, key, fallback) {
  try {
    return JSON.parse(storage.getItem(key)) || fallback;
  } catch {
    return fallback;
  }
}

export function readString(storage, key, fallback) {
  try {
    return storage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
