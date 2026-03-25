const TOKEN_KEY = "jobcopilot.token";

export function readStoredToken() {
  return window.localStorage.getItem(TOKEN_KEY);
}

export function writeStoredToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearStoredToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}
