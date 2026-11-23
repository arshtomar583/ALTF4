/** Save text as a file on the viewer's computer. */
export function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  // The browser reads the URL after the click returns.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
