export function takeVisibleItems<T>(items: readonly T[], visibleCount: number): T[] {
  return items.slice(0, Math.max(0, visibleCount));
}

export function nextVisibleCount(
  current: number,
  pageSize: number,
  total: number,
): number {
  return Math.min(total, Math.max(0, current) + Math.max(0, pageSize));
}
