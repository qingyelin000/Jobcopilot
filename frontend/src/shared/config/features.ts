const enabledValue = (value: string | undefined, fallback: boolean) => {
  if (value === undefined) {
    return fallback;
  }

  return value.toLowerCase() !== "false";
};

export const featureFlags = {
  jobs: enabledValue(import.meta.env.VITE_ENABLE_JOBS, true),
  interview: enabledValue(import.meta.env.VITE_ENABLE_INTERVIEW, true),
};
