/** The pages of the dashboard, in the order of the navigation (plan.md D22). */
export interface PageInfo {
  /** With a trailing slash: the export writes each page as a folder with an index.html. */
  href: string;
  label: string;
}

export const PAGES: readonly PageInfo[] = [
  { href: "/", label: "Live" },
  { href: "/history/", label: "History" },
  { href: "/setup/", label: "Setup" },
  { href: "/system/", label: "System" },
];

const withoutTrailingSlash = (path: string): string => (path.length > 1 ? path.replace(/\/+$/, "") : path) || "/";

/**
 * Whether `href` is the page that `pathname` is on. A page owns its own address and anything
 * below it (`/history/` also covers `/history/2026-10-09/`), except the first, which is only `/`.
 */
export function isCurrentPage(pathname: string, href: string): boolean {
  const here = withoutTrailingSlash(pathname);
  const page = withoutTrailingSlash(href);
  return page === "/" ? here === "/" : here === page || here.startsWith(`${page}/`);
}

/** The page the address belongs to, if it is one of ours. */
export function currentPage(pathname: string): PageInfo | undefined {
  return PAGES.find((page) => isCurrentPage(pathname, page.href));
}
