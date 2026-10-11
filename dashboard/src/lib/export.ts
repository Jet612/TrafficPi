/** The files the Data page can download, as CSV. */
export type ExportKind = "counts" | "passes";

/**
 * Where a file is: the vehicles counted, or the passes at stop signs, over the last `days` local days
 * (`0` is everything). The server works the days out in the camera's time zone, as it does for the
 * page's own numbers, so that the file and the page cover the same days.
 */
export const exportUrl = (kind: ExportKind, days: number): string => `/api/export/${kind}.csv?days=${days}`;
