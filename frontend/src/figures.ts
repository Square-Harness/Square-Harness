// Sandbox figures: the only images the interface shows from model or tool output.
// The server validates the run id and the file name again before serving a PNG.

const EXPERIMENT_FIGURE = /^(?:\.\/)?\.mathagent\/experiments\/(exp-\d{8}-\d{6}-[0-9a-f]{6})\/([A-Za-z0-9][A-Za-z0-9._-]{0,63}\.png)$/

/** The served URL of a figure saved by a sandbox run. */
export const figureUrl = (id: string, name: string) => `/api/experiments/${encodeURIComponent(id)}/${encodeURIComponent(name)}`

/** A workspace path such as .mathagent/experiments/<id>/figure-1.png, as a served URL; '' otherwise. */
export const experimentFigure = (url: string) => {
  const match = EXPERIMENT_FIGURE.exec(url)
  return match ? figureUrl(match[1], match[2]) : ''
}
