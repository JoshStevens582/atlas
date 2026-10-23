export function recoveryCodesPlainText(codes: string[]): string {
  return [
    "Atlas recovery codes",
    "Each code works once if you lose your authenticator app.",
    "",
    ...codes,
    "",
  ].join("\n");
}

export async function copyRecoveryCodes(codes: string[]): Promise<void> {
  await navigator.clipboard.writeText(recoveryCodesPlainText(codes));
}

export function downloadRecoveryCodes(codes: string[], username: string): void {
  const blob = new Blob([recoveryCodesPlainText(codes)], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `atlas-recovery-codes-${username}.txt`;
  anchor.click();
  URL.revokeObjectURL(url);
}
